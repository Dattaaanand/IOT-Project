# Starts the CLOUD laptop: Node-RED + dashboard, SQLite, ML and decision engine.
# The IoT device runs on the other laptop (wds_iot_device.zip). Both connect to the
# online HiveMQ broker set in config.json.
#   powershell -ExecutionPolicy Bypass -File start_all.ps1
#   powershell -ExecutionPolicy Bypass -File start_all.ps1 -Model knn
# No internet? See "Backup without internet" in README.md (-LocalBroker).
param([string]$Model = "auto", [switch]$LocalBroker)

$root = $PSScriptRoot
Set-Location $root

if ($LocalBroker) {
    Write-Host "Local MQTT broker (port 1883)"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; node broker/broker.js"
    Start-Sleep -Seconds 2
}

Write-Host "1/2 Node-RED (http://localhost:1880, dashboard /ui)"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\node-red'; node-red --userDir . --settings settings.js"
Start-Sleep -Seconds 12

Write-Host "2/2 ML analytics + decision engine"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; python -m engine.decision_engine --model $Model"

Start-Sleep -Seconds 3
Start-Process "http://localhost:1880/ui"
Write-Host "Cloud side is up. Now start the IoT device on the other laptop."
