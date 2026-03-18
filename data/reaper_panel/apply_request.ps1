$ErrorActionPreference = 'Stop'
$uri = 'http://127.0.0.1:8000/execute-plan'
$requestPath = 'C:\Users\cenriquez\Desktop\reapergpt\data\reaper_panel\apply_request.json'
$responsePath = 'C:\Users\cenriquez\Desktop\reapergpt\data\reaper_panel\apply_response.json'
$statusPath = 'C:\Users\cenriquez\Desktop\reapergpt\data\reaper_panel\apply_status.json'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
try {
  $requestBody = Get-Content -Raw -Path $requestPath
  $resp = Invoke-WebRequest -UseBasicParsing -Method 'POST' -Uri $uri -ContentType 'application/json' -Body $requestBody
  [System.IO.File]::WriteAllText($responsePath, $resp.Content, $utf8NoBom)
  [System.IO.File]::WriteAllText($statusPath, (([pscustomobject]@{state='ok'; http_status=[int]$resp.StatusCode} | ConvertTo-Json -Compress)), $utf8NoBom)
} catch {
  if ($_.Exception.Response) {
    $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
    $content = $reader.ReadToEnd()
    [System.IO.File]::WriteAllText($responsePath, $content, $utf8NoBom)
    [System.IO.File]::WriteAllText($statusPath, (([pscustomobject]@{state='error'; http_status=[int]$_.Exception.Response.StatusCode.value__; message='HTTP request failed'} | ConvertTo-Json -Compress)), $utf8NoBom)
    exit 1
  }
  [System.IO.File]::WriteAllText($statusPath, (([pscustomobject]@{state='error'; message=$_.Exception.Message} | ConvertTo-Json -Compress)), $utf8NoBom)
  exit 1
}