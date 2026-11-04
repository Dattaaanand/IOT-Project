# How to Run the Project on Two Laptops

- **Your laptop = the cloud:** Node-RED, dashboard, database, ML models, decision engine
- **Friend's laptop = the IoT device:** sends sensor data, receives commands, operates pumps and valves

Both laptops connect to the online HiveMQ broker, so **each one just needs internet**. They don't have to be on the same network.

---

## Part 1: One-time setup

### Your laptop
Everything is already installed, so there's nothing to do here.

### Friend's laptop

**1. Send the zip.** Send `wds_iot_device.zip` from the project folder (`D:\Programming\IotProject`).

Use Google Drive, WhatsApp, Telegram or a pen drive. Don't email it through Gmail, because Gmail blocks zip files containing `.bat` files.

**2. Unzip it.** Right-click the zip → **Extract All**. It must be extracted; it won't run from inside the zip.

**3. Check that Python is installed.** Open Command Prompt and run:

```bash
python --version
```

- It should show Python 3.8 or newer.
- If it shows an error or opens the Microsoft Store, install Python from https://www.python.org/downloads/ and **tick "Add python.exe to PATH"** on the first installer screen. Then close and reopen Command Prompt and check again.

Nothing else needs installing. The MQTT library and the dataset are inside the zip.

---

## Part 2: Run it

### Step 1: Connect both laptops to the internet
A phone hotspot is the safest choice, because college Wi-Fi sometimes blocks MQTT (port 1883). Sharing one hotspot is fine, and so are separate networks.

### Step 2: Start the cloud on your laptop
Open PowerShell and run these two commands:

```bash
cd D:\Programming\IotProject
```

```bash
powershell -ExecutionPolicy Bypass -File start_all.ps1
```

- Two new windows open (Node-RED and the decision engine). **Leave them open**; closing one stops that part of the system.
- It takes about 20 seconds, then the dashboard opens in your browser: http://localhost:1880/ui
- If Windows Firewall asks about Node.js, click **Allow**.

**Check:** the decision engine window shows `[engine] connected to broker (Success)`.

### Step 3: Start the device on your friend's laptop
Open the unzipped `wds_iot_device` folder and **double-click `run_device.bat`**.

- If Windows shows "Windows protected your PC", click **More info → Run anyway**.
- On Mac or Linux, open a terminal in that folder and run `sh run_device.sh`.

**Check:** within a few seconds the window shows:

```
[BROKER]  connected to broker.hivemq.com
[CLOUD]   system state = NORMAL   (model extra_trees, ...)
          sent 47 readings | clean 47/400 | ... | cloud is responding
```

### Step 4: Confirm that they're talking
On your dashboard, click the **☰ menu → Control Panel**. It should say **IoT node: ONLINE**. Then open **Live Monitoring**, where the charts should be moving.

If you see all this, the project is working.

---

## Part 3: Demo

Use the buttons in **Control Panel → Scenario Injection**, then switch to **Analytics & Alerts** to watch. Most scenarios start with normal readings, so the event appears after a short wait. Keep your friend's window visible, because the same events appear there too.

| Button | Wait | Your dashboard shows | Friend's window shows |
|---|---|---|---|
| **Leak 1 (large)** | ~18 s | Red LEAK, severity moderate then severe, zone C valve closed, then back to NORMAL | Alert, commands, `closed valves=['iso_C']`, `leak_isolated`, then `restore` |
| **Sensor fault P2** | a few seconds | Orange SENSOR FAULT on `p2_out`, no valve closed | Alert, `flagged=['p2_out']` |
| **Cyber-attack** | ~22 s | Purple CYBER ATTACK, controller mode SAFE | Alert, `mode=SAFE`, `attack_blocked` |

Other things to show:

1. **Manual override:** in Control Panel, click **Isolate zone A**. The command appears in your friend's window. Click **Restore all** afterwards.
2. **Auto mode off:** turn off **Automatic control actions** and click **Leak 1**. Only "Recommended action" alerts appear and no commands are sent. Turn it back on afterwards.
3. **Switching models:** choose `knn` or `cnn_lstm` in **Active ML model**, then try a leak. Switch back to `extra_trees` when done.
4. **Device going offline:** press **Ctrl+C** in your friend's window. The Control Panel changes to **IoT node: OFFLINE** after a moment. Run `run_device.bat` again to reconnect.
5. **Results:** open the **Performance** page for live KPIs and the model comparison.

---

## Part 4: Stop it

- **Friend's laptop:** press **Ctrl+C**, then close the window.
- **Your laptop:** close the Node-RED and decision engine windows.

Optional, to clear old data before a fresh demo (with everything stopped):

```bash
python -m common.db --reset
```

Run this from the project folder.

---

## If something goes wrong

| What you see | Likely cause | What to do |
|---|---|---|
| `python is not recognized` | Python missing or not on PATH | Install it and tick "Add python.exe to PATH" |
| Friend's window: `waiting for the broker` | No internet, or the network blocks MQTT | Switch that laptop to a phone hotspot |
| Friend's window: `NO REPLY FROM CLOUD YET` | Cloud isn't running or isn't connected | Check that both windows on your laptop are open and connected |
| Control Panel: `IoT node: OFFLINE` | Device not running, or not connected | Start `run_device.bat` and check that it says `connected` |
| Dashboard opens but charts don't move | Device not sending, or Node-RED not connected | In the Node-RED window, look for `Connected to broker` |
| Browser says it can't reach the dashboard | Node-RED is still starting | Wait 20 seconds and refresh http://localhost:1880/ui |
| Button clicks seem to do nothing | The scenario starts with normal readings | Wait up to 25 seconds; see the table in Part 3 |
| HiveMQ or the internet is completely down | | Use the "Backup without internet" steps in `README.md` |

---

## Before the assessment

- Do one full practice run with **both** laptops, ideally on the network you'll use on the day.
- Make sure your friend's laptop has Python installed.
- Charge both laptops and have a phone hotspot ready.
- If you change the broker or topics in `config.json`, rebuild the zip and send it again, because both laptops must match:

```bash
python tools/build_device_zip.py
```
