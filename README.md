<h1 align="center">pico-ducky</h1>

<div align="center">
  <strong>Make a cheap but powerful USB Rubber Ducky with a Raspberry Pi Pico</strong>
</div>

<br />

<div align="center">
  <img alt="GitHub code size in bytes" src="https://img.shields.io/github/languages/code-size/dbisu/pico-ducky">
  <img alt="GitHub license" src="https://img.shields.io/github/license/dbisu/pico-ducky">
  <a href="https://github.com/dbisu/pico-ducky/graphs/contributors"><img alt="GitHub contributors" src="https://img.shields.io/github/contributors/dbisu/pico-ducky"></a>
  <img alt="GitHub commit activity" src="https://img.shields.io/github/commit-activity/m/dbisu/pico-ducky">
  <img alt="GitHub Repo stars" src="https://img.shields.io/github/stars/dbisu/pico-ducky">
</div>

<br />

## Quick Start Guide
Install and have your USB Rubber Ducky working in less than 5 minutes.

1. Download the latest release from the [Releases](https://github.com/dbisu/pico-ducky/releases) page.

2. Plug the device into a USB port while holding the boot button. It will show up as a removable media device named RPI-RP2.

3. Install CircutlPython on the Pico or Pico W

If using a Pico board:

Copy the adafruit-circuitpython-raspberry_pi_pico-en_US-10.0.3.uf2 file to the root of the Pico (RPI-RP2). The device will reboot and after a second or so, it will reconnect as CIRCUITPY.

If using a Pico W board:

Copy the adafruit-circuitpython-raspberry_pi_pico_w-en_US-10.0.3.uf2 file to the root of the Pico (RPI-RP2). The device will reboot and after a second or so, it will reconnect as CIRCUITPY.  

If using a Pico 2 board:

Copy the adafruit-circuitpython-raspberry_pi_pico2-en_US-10.0.3.uf2 file to the root of the Pico (RPI-RP2). The device will reboot and after a second or so, it will reconnect as CIRCUITPY.

If using a Pico 2W board:

Copy the adafruit-circuitpython-raspberry_pi_pico2_w-en_US-10.0.3.uf2 file to the root of the Pico (RPI-RP2). The device will reboot and after a second or so, it will reconnect as CIRCUITPY.

4. Copy the lib folder to the root of the CIRCUITPY

5. Copy *.py to the root of the CIRCUITPY

6. Follow the instructions in README.md to enter setup mode

7. Copy your payload as payload.dd to the root of the CIRCUITPY

8. Unplug the device from the USB port and remove the setup jumper.

Enjoy your Pico-Ducky.

## Setup mode

To edit the payload, enter setup mode by connecting the pin 1 (`GP0`) to pin 3 (`GND`), this will stop the pico-ducky from injecting the payload in your own machine.
The easiest way to do so is by using a jumper wire between those pins as seen bellow.

![Setup mode with a jumper](images/setup-mode.png)

## USB enable/disable mode

If you need the pico-ducky to not show up as a USB mass storage device for stealth, follow these instructions.  
- Enter setup mode.    
- Copy your payload script to the pico-ducky.  
- Disconnect the pico from your host PC.
- Connect a jumper wire between pin 18 (`GND`) and pin 20 (`GPIO15`).  
This will prevent the pico-ducky from showing up as a USB drive when plugged into the target computer.  
- Remove the jumper and reconnect to your PC to reprogram.  

Pico: The default mode is USB mass storage enabled.   
Pico W: The default mode is USB mass storage **disabled**  

![USB enable/disable mode](images/usb-boot-mode.png)


-----

# Full Install Instructions

Install and have your USB Rubber Ducky working in less than 5 minutes.

1. Clone the repo to get a local copy of the files. `git clone https://github.com/dbisu/pico-ducky.git`

2. Download [CircuitPython for the Raspberry Pi Pico](https://circuitpython.org/board/raspberry_pi_pico/). *Updated to 10.0.3  
   Download [CircuitPython for the Raspberry Pi Pico W](https://circuitpython.org/board/raspberry_pi_pico_w/). *Updated to 10.0.3  
   Download [CircuitPython for the Raspberry Pi Pico 2](https://circuitpython.org/board/raspberry_pi_pico2/). *Updated to 10.0.3  
   Download [CircuitPython for the Raspberry Pi Pico 2W](https://circuitpython.org/board/raspberry_pi_pico2_w/). *Updated to 10.0.3  

3. Plug the device into a USB port while holding the boot button. It will show up as a removable media device named `RPI-RP2`.

4. Copy the downloaded `.uf2` file to the root of the Pico (`RPI-RP2`). The device will reboot and after a second or so, it will reconnect as `CIRCUITPY`.

5. Download `adafruit-circuitpython-bundle-10.x-mpy-YYYYMMDD.zip` [here](https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases/latest) and extract it outside the device.

6. Navigate to `lib` in the recently extracted folder and copy `adafruit_hid` to the `lib` folder on your Raspberry Pi Pico.

7. Copy `adafruit_debouncer.mpy` and `adafruit_ticks.mpy` to the `lib` folder on your Raspberry Pi Pico.

8. Copy `asyncio` to the `lib` folder on your Pico.

9. Copy `adafruit_wsgi` to the `lib` folder on your Pico.

10. Copy `boot.py` from your clone to the root of your Pico.

11. Copy `duckyinpython.py`, `code.py`, `pins.py`, `webapp.py`, `wsgiserver.py` to the root folder of the Pico.

12. *For Pico W Only* Create the file `secrets.py` in the root of the Pico W. This contains the AP name and password to be created by the Pico W.
`secrets = { 'ssid' : "BadAPName", 'password' : "badpassword" }`
   Use a long, unguessable password — anyone on the AP can reach the web UI.
   Optionally also create `creds.py` to require login for the web UI (see
   *Web UI authentication* below).

13. Find a script [here](https://github.com/hak5/usbrubberducky-payloads) or [create your own one using Ducky Script](https://docs.hak5.org/hak5-usb-rubber-ducky/ducky-script-basics/hello-world) and save it as `payload.dd` in the Pico. Currently, pico-ducky only supports DuckyScript 1.0, and some of 3.0.

14. Be careful, if your device isn't in [setup mode](#setup-mode), the device will reboot and after half a second, the script will run.

15. **Please note:** by default Pico W will not show as a USB drive

### Pico W Web Service
The Pico W AP defaults to IP address `192.168.4.1`. The web UI is at
`http://192.168.4.1/`. Everything is served inline — no CDNs, no internet.

#### First-run setup wizard
On the very first visit (when `creds.py` is missing) every URL is redirected
to `/setup`. Pick a username, an 8+ character password, and optionally an API
token, and submit. The wizard writes `creds.py` to the device for you and
locks down the UI behind HTTP Basic auth.

#### UI features
- **Dark / light theme** toggle (top-right) with per-browser persistence
- **Payload manager** with size column, live filter, and per-row actions
  (Edit, Preview, Download, Clone, Run, Delete) — every destructive action
  requires confirmation
- **Editor** with snippet quick-insert sidebar, live line/byte counter,
  `Ctrl+S` / `Cmd+S` to save, and automatic draft auto-save to localStorage
  (restored if you reopen with unsaved changes)
- **Syntax-highlighted preview** page (`/preview/<name>`) — commands,
  strings, numbers, `$variables` and operators colorized
- **DuckyScript linter** — runs on save, surfaces typos in command names,
  missing arguments, non-numeric `DELAY` values, and runs of consecutive
  `DELAY`s. Warnings are shown but never block a save.
- **Upload** any local `.dd` file (browser reads it, posts as text)
- **Download** payloads as text attachments
- **Duplicate / Rename / Delete** with proper validation
- **Wipe all** payloads (double-confirm) for fast cleanup
- **Snippets** reference page with one-click copy-to-clipboard
- **Audit log** at `/audit` showing recent actions (auth fails, payload
  edits, runs, reboots, wipes…), with bounded size and a Clear button
- **System** page with board, AP IP, uptime, free/used RAM, CPU temperature,
  filesystem state, connected stations, auth/API status, recent-fail count,
  and a one-click reboot
- **Logout** button forces the browser to drop cached Basic Auth credentials

#### Security hardening
- **HTTP Basic auth** with constant-time credential comparison
- **Login rate limiting** — 5 failed attempts within 5 minutes returns
  `429 Too Many Requests` and an audit entry
- **CSRF tokens** required on every POST endpoint
- **Path-traversal-proof** filenames (`..`, `/`, hidden, non-`.dd` rejected)
- **64 KB hard cap** on payload size; oversize bodies rejected early
- **POST-only** destructive actions (run / delete / write / rename / wipe /
  duplicate / reboot / clear-log)
- **Robust form parser** — survives `=` inside script bodies (was a bug
  in the original implementation)
- **Filesystem writes** wrapped in `try/finally remount-readonly`, so the
  device is *never* left writable after an exception
- **Response headers**: strict `Content-Security-Policy`,
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, and a
  restrictive `Permissions-Policy`
- **HTML escaping everywhere** — payload names and contents can never inject
  HTML into the UI
- **Audit log** at `/audit.log` for forensic review

#### Routes
```
GET  /setup         POST    first-run credential wizard
GET  /                      list payloads
GET  /new           POST    create a new script
GET  /edit/<name>           open the editor
POST /write/<name>          save changes (CSRF)
POST /delete/<name>         delete a payload (CSRF)
POST /duplicate/<name>      clone a payload (CSRF)
GET  /rename/<name>  POST   rename a payload (CSRF on POST)
GET  /download/<name>       download as text/plain
GET  /preview/<name>        syntax-highlighted view
POST /run/<name>            execute now (CSRF on POST, GET also accepted)
POST /wipe                  delete every .dd file (CSRF)
GET  /upload                upload from local file
GET  /snippets              DuckyScript snippet library
GET  /audit                 view audit log
POST /audit/clear           clear audit log (CSRF)
GET  /system                live device status
POST /system/reboot         soft reboot (CSRF)
GET  /logout                drop cached Basic Auth credentials
```

#### Machine-friendly API
All `/api/*` endpoints accept either the web UI's Basic Auth or — when
`API_TOKEN` is set in `creds.py` — `Authorization: Bearer <token>` or a
`?token=<token>` query parameter.

```
GET  /api/payloads          tab-separated list of name<TAB>size
GET  /api/run/<filenumber>  run payload N (1..4 mapped to payload[N].dd)
GET  /api/system            JSON of every System-page stat
```

### Web UI authentication

The fastest way is to let the **setup wizard** create `creds.py` for you on
first visit. If you'd rather provision it manually, drop a `creds.py` next
to `code.py` on the device:

```py
WEB_USERNAME = "admin"
WEB_PASSWORD = "use-a-strong-password"
API_TOKEN    = "optional-32-char-token-for-automation"
```

When `creds.py` is present, every web request requires HTTP Basic
credentials. If absent, the device boots into setup mode on first visit
and refuses to do anything else until you set credentials.

### Keyboard shortcuts (editor)
- `Ctrl+S` / `Cmd+S` — save
- Click a snippet in the sidebar — insert at cursor
- Reopen a payload after closing the tab without saving — the editor
  offers to restore your unsaved draft

## Setup mode

To edit the payload, enter setup mode by connecting the pin 1 (`GP0`) to pin 3 (`GND`), this will stop the pico-ducky from injecting the payload in your own machine.
The easiest way to do so is by using a jumper wire between those pins as seen bellow.

![Setup mode with a jumper](images/setup-mode.png)

## USB enable/disable mode

If you need the pico-ducky to not show up as a USB mass storage device for stealth, follow these instructions.  
- Enter setup mode.    
- Copy your payload script to the pico-ducky.  
- Disconnect the pico from your host PC.
- Connect a jumper wire between pin 18 (`GND`) and pin 20 (`GPIO15`).  
This will prevent the pico-ducky from showing up as a USB drive when plugged into the target computer.  
- Remove the jumper and reconnect to your PC to reprogram.  

Pico: The default mode is USB mass storage enabled.   
Pico W: The default mode is USB mass storage **disabled**  

![USB enable/disable mode](images/usb-boot-mode.png)

## Multiple payloads

Multiple payloads can be stored on the Pico and Pico W.  
To select a payload, ground one of these pins:
- GP4 - payload.dd
- GP5 - payload2.dd
- GP10 - payload3.dd
- GP11 - payload4.dd

## Changing Keyboard Layouts

Copied from [Neradoc/Circuitpython_Keyboard_Layouts](https://github.com/Neradoc/Circuitpython_Keyboard_Layouts/blob/main/PICODUCKY.md)  

#### How to use one of these layouts with the pico-ducky repository.

**Go to the [latest release page](https://github.com/Neradoc/Circuitpython_Keyboard_Layouts/releases/latest), look if your language is in the list.**

#### If your language/layout is in the bundle

Download the `py` zip, named `circuitpython-keyboard-layouts-py-XXXXXXXX.zip`

**NOTE: You can use the mpy version targetting the version of Circuitpython that is on the device, but on Raspberry Pi Pico you don't need it - they only reduce file size and memory use on load, which the pico has plenty of.**

#### If your language/layout is not in the bundle

Try the online generator, it should get you a zip file with the bundles for yout language

https://www.neradoc.me/layouts/

#### Now you have a zip file

#### Find your language/layout in the lib directory

For a language `LANG`, copy the following files from the zip's `lib` folder to the `lib` directory of the board.  
**DO NOT** modify the adafruit_hid directory. Your files go directly in `lib`.  
**DO NOT** change the names or extensions of the files. Just pick the right ones.  
Replace `LANG` with the letters for your language of choice.

- `keyboard_layout_win_LANG.py`
- `keycode_win_LANG.py`

Don't forget to get [the adafruit_hid library](https://github.com/adafruit/Adafruit_CircuitPython_HID/releases/latest).

This is what it should look like **if your language is French for example**.

![CIRCUITPY drive screenshot](https://github.com/Neradoc/Circuitpython_Keyboard_Layouts/raw/main/docs/drive_pico_ducky.png)

#### Modify the pico-ducky code to use your language file:

At the start of the file comment out these lines:

```py
from adafruit_hid.keyboard_layout_us import KeyboardLayoutUS as KeyboardLayout
from adafruit_hid.keycode import Keycode
```

Uncomment these lines:  
*Replace `LANG` with the letters for your language of choice. The name must match the file (without the py or mpy extension).*
```py
from keyboard_layout_win_LANG import KeyboardLayout
from keycode_win_LANG import Keycode
```

##### Example:  Set to German Keyboard (WIN_DE)

```py
from keyboard_layout_win_de import KeyboardLayout
from keycode_win_de import Keycode
```

Copy the files keyboard_layout_win_de.mpy and keycode_win_de.mpy to the /lib folder on the Pico board
```
adafruit_hid/
keyboard_layout_win_de.mpy
keycode_win_de.mpy
```



## Useful links and resources

### How to recover your Pico if it becomes corrupted or doesn't boot.

[Reset Instructions](RESET.md)

### Installation Tool

[ryo-yamada](https://github.com/ryo-yamada) Created a tool to convert a blank RPi Pico to a ducky.  
You can find the tool [here](https://github.com/ryo-yamada/PicoDuckyBuilder)

### Docs

[CircuitPython](https://docs.circuitpython.org/en/latest/README.html)

[CircuitPython HID](https://learn.adafruit.com/circuitpython-essentials/circuitpython-hid-keyboard-and-mouse)

[Ducky Script](https://github.com/hak5darren/USB-Rubber-Ducky/wiki/Duckyscript)

### Video tutorials

[pico-ducky tutorial by **NetworkChuck**](https://www.youtube.com/watch?v=e_f9p-_JWZw)

[USB Rubber Ducky playlist by **Hak5**](https://www.youtube.com/playlist?list=PLW5y1tjAOzI0YaJslcjcI4zKI366tMBYk)

[CircuitPython tutorial on the Raspberry Pi Pico by **DroneBot Workshop**](https://www.youtube.com/watch?v=07vG-_CcDG0)


## Related Projects

[Defcon31-ducky](https://github.com/iot-pwn/defcon31-ducky)  
