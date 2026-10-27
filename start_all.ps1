# Starts the whole pipeline, each component in its own window.
#   powershell -ExecutionPolicy Bypass -File start_all.ps1            (5 readings/s)
#   powershell -ExecutionPolicy Bypass -File start_all.ps1 -Rate 10
param([double]$Rate = 5, [string]$Model = "auto")

$root = $PSScriptRoot
Set-Location $root

Write-Host "1/4 MQTT broker (port 1883)"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; node broker/broker.js"
Start-Sleep -Seconds 2

Write-Host "2/4 Node-RED (http://localhost:1880, dashboard /ui)"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\node-red'; node-red --userDir . --settings settings.js"
Start-Sleep -Seconds 12

Write-Host "3/4 ML analytics + decision engine"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; python -m engine.decision_engine --model $Model"
Start-Sleep -Seconds 4

Write-Host "4/4 IoT node simulator (sensors + actuators)"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root'; python -m simulator.iot_node --rate $Rate"

Start-Sleep -Seconds 3
Start-Process "http://localhost:1880/ui"
