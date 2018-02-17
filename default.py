import xbmc
import xbmcgui
import xbmcaddon
import time
import sys
import colorsys
import os
import datetime
import math
from threading import Timer

__addon__      = xbmcaddon.Addon()
__addondir__   = xbmc.translatePath( __addon__.getAddonInfo('profile') ) 
__addonversion__ = __addon__.getAddonInfo('version')
__cwd__        = __addon__.getAddonInfo('path')
__resource__   = xbmc.translatePath( os.path.join( __cwd__, 'resources', 'lib' ) )

sys.path.append (__resource__)

from settings import *
from tools import *
from hue import *

try:
  import requests
except ImportError:
  xbmc.log("ERROR: Could not locate required library requests")
  notify("Kodi Hue", "ERROR: Could not import Python requests")

xbmc.log("Kodi Hue service started, version: %s" % __addonversion__)

capture = xbmc.RenderCapture()
fmt = capture.getImageFormat()
# BGRA or RGBA
# xbmc.log("Hue Capture Image format: %s" % fmt)
fmtRGBA = fmt == 'RGBA'

class RepeatedTimer(object):
  def __init__(self, interval, function, *args, **kwargs):
    self._timer     = None
    self.interval   = interval
    self.function   = function
    self.args       = args
    self.kwargs     = kwargs
    self.is_running = False
    self.start()

  def _run(self):
    self.is_running = False
    self.start()
    self.function(*self.args, **self.kwargs)

  def start(self):
    if not self.is_running:
      self._timer = Timer(self.interval, self._run)
      self._timer.start()
      self.is_running = True

  def stop(self):
    self._timer.cancel()
    self.is_running = False

class MyMonitor( xbmc.Monitor ):
  def __init__( self, *args, **kwargs ):
    xbmc.Monitor.__init__( self )

  def onSettingsChanged( self ):
    xbmc.log("running in mode %s" % str(hue.settings.mode))
    last = datetime.datetime.now()
    hue.settings.readxml()
    hue.update_settings()

class MyPlayer(xbmc.Player):
  duration = 0
  playingvideo = False
  playlistlen = 0
  timer = None
  movie = False

  def __init__(self):
    xbmc.Player.__init__(self)
  
  def checkTime(self):
    if self.isPlayingVideo():
      check_time(int(self.getTime())) #call back out to plugin function.

  def onPlayBackStarted(self):
    xbmc.log("Kodi Hue: DEBUG playback started called on player")
    playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
    self.playlistlen = playlist.size()
    self.playlistpos = playlist.getposition()

    if self.isPlayingVideo() and not self.playingvideo:
      self.playingvideo = True
      self.duration = self.getTotalTime()
      self.movie = xbmc.getCondVisibility('VideoPlayer.Content(movies)')

      global credits_triggered
      credits_triggered = False
      if self.movie and self.duration != 0: #only try if its a movie and has a duration
        get_credits_info(self.getVideoInfoTag().getTitle(), self.duration) # TODO: start it on a timer to not block the beginning of the media
        xbmc.log("credits_time: %r" % credits_time)
        self.timer = RepeatedTimer(1, self.checkTime)
      state_changed("started", self.duration)

  def onPlayBackPaused(self):
    xbmc.log("Kodi Hue: DEBUG playback paused called on player")
    if self.isPlayingVideo():
      self.playingvideo = False
      if self.movie and not self.timer is None:
        self.timer.stop()
      state_changed("paused", self.duration)

  def onPlayBackResumed(self):
    xbmc.log("playback resumed called on player")
    if self.isPlayingVideo():
      self.playingvideo = True
      if self.duration == 0:
        self.duration = self.getTotalTime()
        if self.movie and self.duration != 0: #only try if its a movie and has a duration
          get_credits_info(self.getVideoInfoTag().getTitle(), self.duration) # TODO: start it on a timer to not block the beginning of the media
          xbmc.log("credits_time: %r" % credits_time)
      if self.movie and self.duration != 0:    
        self.timer = RepeatedTimer(1, self.checkTime)
      state_changed("resumed", self.duration)

  def onPlayBackStopped(self):
    xbmc.log("Kodi Hue: DEBUG playback stopped called on player")
    self.playingvideo = False
    self.playlistlen = 0
    if self.movie and not self.timer is None:
      self.timer.stop()
    state_changed("stopped", self.duration)

  def onPlayBackEnded(self):
    xbmc.log("Kodi Hue: DEBUG playback ended called on player")
    # If there are upcoming plays, ignore 
    if self.playlistpos < self.playlistlen-1:
      return
      
    self.playingvideo = False
    if self.movie and not self.timer is None:
      self.timer.stop()
    state_changed("stopped", self.duration)

class HSVRatio:
  cyan_min = float(4.5/12.0)
  cyan_max = float(7.75/12.0)

  def __init__(self, hue=0.0, saturation=0.0, value=0.0, ratio=0.0):
    self.h = hue
    self.s = saturation
    self.v = value
    self.ratio = ratio

  def average(self, h, s, v):
    self.h = (self.h + h)/2
    self.s = (self.s + s)/2
    self.v = (self.v + v)/2

  def averageValue(self, overall_value):
    if self.ratio > 0.5:
      self.v = self.v * self.ratio + overall_value * (1-self.ratio)
    else:
      self.v = (self.v + overall_value)/2
    

  def hue(self, fullSpectrum):
    if fullSpectrum != True:
      if self.s > 0.01:
        if self.h < 0.5:
          #yellow-green correction
          self.h = self.h * 1.17
          #cyan-green correction
          if self.h > self.cyan_min:
            self.h = self.cyan_min
        else:
          #cyan-blue correction
          if self.h < self.cyan_max:
            self.h = self.cyan_max

    h = int(self.h*65535) # on a scale from 0 <-> 65535
    s = int(self.s*255)
    v = int(self.v*255)
    if v < hue.settings.ambilight_min:
      v = hue.settings.ambilight_min
    if v > hue.settings.ambilight_max:
      v = hue.settings.ambilight_max
    return h, s, v

  def __repr__(self):
    return 'h: %s s: %s v: %s ratio: %s' % (self.h, self.s, self.v, self.ratio)

class Screenshot:
  def __init__(self, pixels, capture_width, capture_height):
    self.pixels = pixels
    self.capture_width = capture_width
    self.capture_height = capture_height

  def most_used_spectrum(self, spectrum, saturation, value, size, overall_value):
    # color bias/groups 6 - 36 in steps of 3
    colorGroups = settings.color_bias
    if colorGroups == 0:
      colorGroups = 1
    colorHueRatio = 360 / colorGroups

    hsvRatios = []
    hsvRatiosDict = {}

    for i in range(360):
      if spectrum.has_key(i):
        #shift index to the right so that groups are centered on primary and secondary colors
        colorIndex = int(((i+colorHueRatio/2) % 360)/colorHueRatio)
        pixelCount = spectrum[i]

        if hsvRatiosDict.has_key(colorIndex):
          hsvr = hsvRatiosDict[colorIndex]
          hsvr.average(i/360.0, saturation[i], value[i])
          hsvr.ratio = hsvr.ratio + pixelCount / float(size)

        else:
          hsvr = HSVRatio(i/360.0, saturation[i], value[i], pixelCount / float(size))
          hsvRatiosDict[colorIndex] = hsvr
          hsvRatios.append(hsvr)

    colorCount = len(hsvRatios)
    if colorCount > 1:
      # sort colors by popularity
      hsvRatios = sorted(hsvRatios, key=lambda hsvratio: hsvratio.ratio, reverse=True)
      # xbmc.log("hsvRatios %s" % hsvRatios)
      
      #return at least 3
      if colorCount == 2:
        hsvRatios.insert(0, hsvRatios[0])
      
      hsvRatios[0].averageValue(overall_value)
      hsvRatios[1].averageValue(overall_value)
      hsvRatios[2].averageValue(overall_value)
      return hsvRatios

    elif colorCount == 1:
      hsvRatios[0].averageValue(overall_value)
      return [hsvRatios[0]] * 3

    else:
      return [HSVRatio()] * 3

  def spectrum_hsv(self, pixels, width, height):
    spectrum = {}
    saturation = {}
    value = {}

    size = int(len(pixels)/4)
    pixel = 0

    i = 0
    s, v = 0, 0
    r, g, b = 0, 0, 0
    tmph, tmps, tmpv = 0, 0, 0
    
    for i in range(size):
      if fmtRGBA:
        r = pixels[pixel]
        g = pixels[pixel + 1]
        b = pixels[pixel + 2]
      else: #probably BGRA
        b = pixels[pixel]
        g = pixels[pixel + 1]
        r = pixels[pixel + 2]
      pixel += 4

      tmph, tmps, tmpv = colorsys.rgb_to_hsv(float(r/255.0), float(g/255.0), float(b/255.0))
      s += tmps
      v += tmpv

      # skip low value and saturation
      if tmpv > 0.25:
        if tmps > 0.33:
          h = int(tmph * 360)

          # xbmc.log("%s \t set pixel r %s \tg %s \tb %s" % (i, r, g, b))
          # xbmc.log("%s \t set pixel h %s \ts %s \tv %s" % (i, tmph*100, tmps*100, tmpv*100))

          if spectrum.has_key(h):
            spectrum[h] += 1 # tmps * 2 * tmpv
            saturation[h] = (saturation[h] + tmps)/2
            value[h] = (value[h] + tmpv)/2
          else:
            spectrum[h] = 1 # tmps * 2 * tmpv
            saturation[h] = tmps
            value[h] = tmpv

    overall_value = v / float(i)
    # s_overall = int(s * 100 / i)
    return self.most_used_spectrum(spectrum, saturation, value, size, overall_value)

def run():
  player = MyPlayer()
  if player == None:
    logger.log("Cannot instantiate player. Bailing out")
    return
    
  monitor = MyMonitor()

  last = 0

  #xbmc.log("starting run loop!")
  while not monitor.abortRequested():

    waitTimeout = 1;

    if hue.settings.mode == 0: # ambilight mode
      waitTimeout = 0.1
      now = time.time()
      #xbmc.log("run loop delta: %f (%f/sec)" % ((now-last), 1/(now-last)))
      last = now

      if player.playingvideo: # only if there's actually video
        try:
          buf = capture.getImage(200)
          if len(buf) > 0 and player.playingvideo:
            screen = Screenshot(buf, capture.getWidth(), capture.getHeight())
            hsvRatios = screen.spectrum_hsv(screen.pixels, screen.capture_width, screen.capture_height)
            if hue.settings.light == 0:
              fade_light_hsv(hue.light, hsvRatios[0])
            else:
              fade_light_hsv(hue.light[0], hsvRatios[0])
              if hue.settings.light > 1:
                #xbmc.sleep(4) #why?
                fade_light_hsv(hue.light[1], hsvRatios[1])
              if hue.settings.light > 2:
                #xbmc.sleep(4) #why?
                fade_light_hsv(hue.light[2], hsvRatios[2])
        except ZeroDivisionError:
          #xbmc.log("no frame. looping.")
          pass

    if monitor.waitForAbort(waitTimeout):
      break
      
  del player
  del monitor

def fade_light_hsv(light, hsvRatio):
  fullSpectrum = light.fullSpectrum
  h, s, v = hsvRatio.hue(fullSpectrum)
  hvec = abs(h - light.hueLast) % int(65535/2)
  hvec = float(hvec/128.0)
  svec = s - light.satLast
  vvec = v - light.valLast
  distance = math.sqrt(hvec**2 + svec**2 + vvec**2) #changed to squares for performance
  if distance > 0:
    duration = int(3 + 27 * distance/255)
    # xbmc.log("distance %s duration %s" % (distance, duration))
    light.set_light2(h, s, v, duration)

credits_time = None #test = 10
credits_triggered = False

def get_credits_info(title, duration):
  xbmc.log("get_credits_info")
  if hue.settings.undim_during_credits:
    #get credits time here
    xbmc.log("title: %r, duration: %r" % (title, duration))
    global credits_time
    credits_time = ChapterManager.CreditsStartTimeForMovie(title, duration)
    xbmc.log("set credits time to: %r" % credits_time)

def check_time(cur_time):
  global credits_triggered
  #xbmc.log("check_time: %r, undim: %r, credits_time: %r" % (cur_time, hue.settings.undim_during_credits, credits_time))
  if hue.settings.undim_during_credits and credits_time != None:
    if (cur_time >= credits_time + hue.settings.credits_delay_time) and not credits_triggered:
      xbmc.log("hit credits, turn on lights")
      # do partial undim (if enabled, otherwise full undim)
      if hue.settings.mode == 0 and hue.settings.ambilight_dim:
        if hue.settings.ambilight_dim_light == 0:
          hue.ambilight_dim_light.brighter_light()
      elif hue.settings.ambilight_dim_light > 0:
        for l in hue.ambilight_dim_light:
          l.brighter_light()
      else:
        hue.brighter_lights()
      credits_triggered = True
    elif (cur_time < credits_time + hue.settings.credits_delay_time) and credits_triggered:
      #still before credits, if this has happened, we've rewound
      credits_triggered = False

def state_changed(state, duration):
  xbmc.log("state changed to: %s" % state)

  if duration < hue.settings.misc_disableshort_threshold and hue.settings.misc_disableshort:
    xbmc.log("add-on disabled for short movies")
    return

  if state == "started":
    xbmc.log("retrieving current setting before starting")
    
    if hue.settings.light == 0: # group mode
      hue.light.get_current_setting()
    else:
      for l in hue.light:
        l.get_current_setting() #loop through without sleep.
      # hue.light[0].get_current_setting()
      # if hue.settings.light > 1:
      #   xbmc.sleep(1)
      #   hue.light[1].get_current_setting()
      # if hue.settings.light > 2:
      #   xbmc.sleep(1)
      #   hue.light[2].get_current_setting()

    if hue.settings.mode == 0: # ambilight mode
      if hue.settings.ambilight_dim:
        if hue.settings.ambilight_dim_light == 0:
          hue.ambilight_dim_light.get_current_setting()
        elif hue.settings.ambilight_dim_light > 0:
          for l in hue.ambilight_dim_light:
            l.get_current_setting()
      #start capture when playback starts
      capture_width = 128
      capture_height = capture_width / capture.getAspectRatio()
      if capture_height == 0:
        capture_height = capture_width #fix for divide by zero.
      xbmc.log("capture %s x %s" % (capture_width, capture_height))
      capture.capture(int(capture_width), int(capture_height))

  if (state == "started" and hue.pauseafterrefreshchange == 0) or state == "resumed":
    if hue.settings.mode == 0 and hue.settings.ambilight_dim: #if in ambilight mode and dimming is enabled
      xbmc.log("dimming for ambilight")
      if hue.settings.ambilight_dim_light == 0:
        hue.ambilight_dim_light.dim_light()
      elif hue.settings.ambilight_dim_light > 0:
        for l in hue.ambilight_dim_light:
          l.dim_light()
    hue.dim_lights()
  elif state == "paused" and hue.last_state == "dimmed":
    #only if its coming from being off
    if hue.settings.mode == 0 and hue.settings.ambilight_dim:
      if hue.settings.ambilight_dim_light == 0:
        hue.ambilight_dim_light.partial_light()
      elif hue.settings.ambilight_dim_light > 0:
        for l in hue.ambilight_dim_light:
          l.partial_light()
    hue.partial_lights()
  elif state == "stopped":
    if hue.settings.mode == 0 and hue.settings.ambilight_dim:
      if hue.settings.ambilight_dim_light == 0:
        hue.ambilight_dim_light.brighter_light()
      elif hue.settings.ambilight_dim_light > 0:
        for l in hue.ambilight_dim_light:
          l.brighter_light()
    hue.brighter_lights()

if ( __name__ == "__main__" ):
  logger = Logger()
  settings = MySettings()
  if settings.debug == True:
    logger.debug()
 
  args = None
  if len(sys.argv) == 2:
    args = sys.argv[1]
  hue = Hue(settings, args)
  while not hue.connected and not monitor.abortRequested():
    xbmc.log("not connected")
    time.sleep(1)
  run()
  
  del logger
  del settings

