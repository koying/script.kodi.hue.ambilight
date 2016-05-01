import time
import os
import socket
import json
import random
import hashlib
NOSE = os.environ.get('NOSE', None)
if not NOSE:
  import xbmc
  import xbmcaddon

  __addon__      = xbmcaddon.Addon()
  __cwd__        = __addon__.getAddonInfo('path')
  __icon__       = os.path.join(__cwd__,"icon.png")
  __settings__   = os.path.join(__cwd__,"resources","settings.xml")
  __xml__        = os.path.join( __cwd__, 'addon.xml' )

def notify(title, msg=""):
  if not NOSE:
    global __icon__
    xbmc.executebuiltin("XBMC.Notification(%s, %s, 3, %s)" % (title, msg, __icon__))

try:
  import requests
except ImportError:
  notify("Kodi Hue", "ERROR: Could not import Python requests")

def get_version():
  # prob not the best way...
  global __xml__
  try:
    for line in open(__xml__):
      if line.find("ambilight") != -1 and line.find("version") != -1:
        return line[line.find("version=")+9:line.find(" provider")-1]
  except:
    return "unknown"

def register_user(hue_ip):
  device = "xbmc-player"
  data = '{"devicetype": "%s"}' % device

  r = requests.post('http://%s/api' % hue_ip, data=data)
  response = r.text
  while "link button not pressed" in response:
    notify("Bridge discovery", "press link button on bridge")
    r = requests.post('http://%s/api' % hue_ip, data=data)
    response = r.text 
    time.sleep(3)

  j = r.json()
  username = [0]["success"]["username"];
  return username

class Light:
  start_setting = None
  group = False
  livingwhite = False
  fullSpectrum = False

  def __init__(self, light_id, settings):
    self.logger = Logger()
    if settings.debug:
      self.logger.debug()

    self.bridge_ip    = settings.bridge_ip
    self.bridge_user  = settings.bridge_user
    self.light        = light_id
    self.dim_time     = settings.dim_time
    self.override_hue = settings.override_hue
    self.dimmed_bri   = settings.dimmed_bri
    self.dimmed_hue   = settings.dimmed_hue
    self.override_paused = settings.override_paused
    self.paused_bri   = settings.paused_bri
    self.undim_bri    = settings.undim_bri
    self.undim_hue    = settings.undim_hue
    self.override_undim_bri = settings.override_undim_bri
    self.onLast = True
    self.hueLast = 0
    self.satLast = 0
    self.valLast = 255

    self.get_current_setting()
    self.s = requests.Session()

  def request_url_put(self, url, data):
    if self.start_setting['on']:
      try:
        self.s.put(url, data=data)
      except:
        self.logger.debuglog("exception in request_url_put")
        pass # probably a timeout

  def get_current_setting(self):
    r = requests.get("http://%s/api/%s/lights/%s" % \
      (self.bridge_ip, self.bridge_user, self.light))
    j = r.json()
    self.start_setting = {}
    state = j['state']
    self.start_setting['on'] = state['on']
    self.start_setting['bri'] = state['bri']
    self.onLast = state['on']
    self.valLast = state['bri']
    
    modelid = j['modelid']
    self.fullSpectrum = ((modelid == 'LST001') or (modelid == 'LST002') or (modelid == 'LLC007'))

    if state.has_key('hue'):
      self.start_setting['hue'] = state['hue']
      self.start_setting['sat'] = state['sat']
      self.hueLast = state['hue']
      self.satLast = state['sat']
    
    else:
      self.livingwhite = True

  # def set_light(self, data):
  #   self.logger.debuglog("set_light: %s: %s" % (self.light, data))
  #   self.request_url_put("http://%s/api/%s/lights/%s/state" % \
  #     (self.bridge_ip, self.bridge_user, self.light), data=data)

  def set_light2(self, hue, sat, bri, dur=20):
    data = {}

    if not self.livingwhite:
      if not hue is None:
        data["hue"] = hue
        self.hueLast = hue
      if not sat is None:
        data["sat"] = sat
        self.satLast = sat

    if bri > 0:
      data["on"] = True
      self.onLast = True
      data["bri"] = bri
      self.valLast = bri
    else:
      data["on"] = False
      self.onLast = False
      self.valLast = bri

    data["transitiontime"] = dur
    
    dataString = json.dumps(data)

    self.logger.debuglog("set_light2: %s: %s" % (self.light, dataString))
    
    self.request_url_put("http://%s/api/%s/lights/%s/state" % \
      (self.bridge_ip, self.bridge_user, self.light), data=dataString)

  def flash_light(self):
    self.dim_light()
    time.sleep(self.dim_time/10)
    self.brighter_light()

  def dim_light(self):
    if self.override_hue:
      hue = self.dimmed_hue
    else:
      hue = None

    self.set_light2(hue, None, self.dimmed_bri, self.dim_time)

  def brighter_light(self):
    if self.override_undim_bri:
      bri = self.undim_bri
    else:
      bri = self.start_setting['bri']

    if not self.livingwhite:
      sat = self.start_setting['sat']

      if self.override_hue:
        hue = self.undim_hue
      else:
        hue = self.start_setting['hue']
    else:
      sat = None
      hue = None

    self.set_light2(hue, sat, bri, self.dim_time)

  def partial_light(self):
    if self.override_paused:
      bri = self.paused_bri
      if not self.livingwhite:
        sat = self.start_setting['sat']

        if self.override_hue:
          hue = self.undim_hue
        else:
          hue = self.start_setting['hue']
      else:
        sat = None
        hue = None
      
      self.set_light2(hue, sat, bri, self.dim_time)
    else:
      #not enabled for dimming on pause
      self.brighter_light()

class Group(Light):
  group = True
  lights = {}

  def __init__(self, settings):
    self.group_id = settings.group_id

    self.logger = Logger()
    if settings.debug:
      self.logger.debug()

    Light.__init__(self, settings.light1_id, settings)
    
    for light in self.get_lights():
      tmp = Light(light, settings)
      tmp.get_current_setting()
      if tmp.start_setting['on']:
        self.lights[light] = tmp

  def __len__(self):
    return 0

  def get_lights(self):
    try:
      r = requests.get("http://%s/api/%s/groups/%s" % \
        (self.bridge_ip, self.bridge_user, self.group_id))
      j = r.json()
    except:
      self.logger.debuglog("WARNING: Request fo bridge failed")
      #notify("Communication Failed", "Error while talking to the bridge")

    try:
      return j['lights']
    except:
      # user probably selected a non-existing group
      self.logger.debuglog("Exception: no lights in this group")
      return []

  # def set_light(self, data):
  #   self.logger.debuglog("set_light: %s" % data)
  #   Light.request_url_put(self, "http://%s/api/%s/groups/%s/action" % \
  #     (self.bridge_ip, self.bridge_user, self.group_id), data=data)

  def set_light2(self, hue, sat, bri, dur=20):

    data = {}

    if not self.livingwhite:
      if not hue is None:
        data["hue"] = hue
        self.hueLast = hue
      if not sat is None:
        data["sat"] = sat
        self.satLast = sat

    if bri > 0:
      data["on"] = True
      self.onLast = True
      data["bri"] = bri
      self.valLast = bri
    else:
      data["on"] = False
      self.onLast = False
      self.valLast = bri

    data["transitiontime"] = dur
    
    dataString = json.dumps(data)

    self.logger.debuglog("set_light2: group_id %s: %s" % (self.group_id, dataString))
    
    self.request_url_put("http://%s/api/%s/groups/%s/action" % \
      (self.bridge_ip, self.bridge_user, self.group_id), data=dataString)

  # def dim_light(self):
  #   for light in self.lights:
  #       self.lights[light].dim_light()

  # def brighter_light(self):
  #     for light in self.lights:
  #       self.lights[light].brighter_light()

  # def partial_light(self):
  #     for light in self.lights:
  #       self.lights[light].partial_light()

  def request_url_put(self, url, data):
    try:
      self.s.put(url, data=data)
    except Exception as e:
      # probably a timeout
      self.logger.debuglog("WARNING: Request fo bridge failed")
      pass

class Logger:
  scriptname = "Kodi Hue"
  enabled = True
  debug_enabled = False

  def log(self, msg):
    if self.enabled:
      xbmc.log("%s: %s" % (self.scriptname, msg))

  def debuglog(self, msg):
    if self.debug_enabled:
      self.log("DEBUG %s" % msg)

  def debug(self):
    self.debug_enabled = True

  def disable(self):
    self.enabled = False
