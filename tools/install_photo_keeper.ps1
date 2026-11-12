# מתקין את "שומר התמונות" כמשימה מתוזמנת ב-Windows (tools\photo_keeper.py).
#   להתקנה:  powershell -ExecutionPolicy Bypass -File tools\install_photo_keeper.ps1
#   להסרה:   powershell -ExecutionPolicy Bypass -File tools\install_photo_keeper.ps1 -Remove
# מה זה עושה: כל יום ב-05:40 — תמונות לפריטים חדשים מאתרי טימברלנד / נאוטיקה / אימפוריום, וכל תמונה שעוד לא אצלנו
# מועתקת לגלריה שלנו (מאגר זה) והאפליקציות עוברות להציג את העותק שלנו. המחשב צריך להיות דלוק (אם היה כבוי — רץ כשנכנסים).
# בלי סיסמאות: רץ בשם המשתמש, רק כשמחובר. יומן: C:\Users\oreln\ChampionTools\logs\photo-keeper.log
param([switch]$Remove, [string]$At = '05:40')
$ErrorActionPreference = 'Stop'
$name = 'Champion Photo Keeper'
if ($Remove) { Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue; Write-Host "הוסר: $name"; exit 0 }
$py = (Get-Command python -ErrorAction Stop).Source
$script = Join-Path $PSScriptRoot 'photo_keeper.py'
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$script`"" -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 4) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'תמונות מוצר: עותק שלנו לכל תמונה + תמונות לפריטים חדשים (champion-images\tools\photo_keeper.py)' -Force | Out-Null
Write-Host "הותקן: '$name' — כל יום ב-$At"
