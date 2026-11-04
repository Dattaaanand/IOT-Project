SMART WATER DISTRIBUTION - IOT DEVICE LAPTOP
=============================================

This laptop plays the IoT device in the water network:
  * sends sensor readings to the online HiveMQ broker
  * receives control commands from the cloud laptop and operates pumps and valves
  * shows the alerts and results the cloud sends back

WHAT YOU NEED
  * Python 3.8 or newer (https://www.python.org/downloads/ - on Windows, tick
    "Add python.exe to PATH" during installation)
  * Internet access
  Nothing else needs installing. The MQTT library and the dataset are in this folder.

HOW TO RUN
  1. Unzip this folder (do not run it from inside the zip).
  2. Windows: double-click run_device.bat
     Mac/Linux: run  sh run_device.sh  in a terminal
  3. To stop: press Ctrl+C

Order does not matter, but both laptops must be running to see results. The cloud
laptop starts with start_all.ps1.

WHAT YOU WILL SEE
  [BROKER]    connection to HiveMQ
  [DATA]      which recording is being sent
  [CLOUD]     the system state decided by the cloud's ML model
  [ALERT]     alerts from the cloud (leak, sensor fault, cyber-attack)
  [COMMAND]   a control command received from the cloud
  [ACTUATOR]  the command was carried out, with the new pump and valve state
  [EFFECT]    the result of the action on the network (e.g. leak contained)

  Every 5 seconds a progress line shows the readings sent. If it says
  "NO REPLY FROM CLOUD YET", the cloud laptop is not running or not connected.

  All results are also saved to results_log.txt in this folder.

OPTIONS
  python iot_device.py --rate 10             send 10 readings per second (default 5)
  python iot_device.py --scenario leak1      keep playing one recording
  Recordings: clean, leak1, leak2, leak3, sensor1, sensor23, sensor45, sensor67, cyber

PROBLEMS
  "python is not recognized"  Install Python and tick "Add python.exe to PATH".
  "waiting for the broker"    Check the internet. Some college or office networks block
                              MQTT port 1883; try a phone hotspot.
