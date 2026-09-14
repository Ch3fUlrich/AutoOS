$dirs = @('C:\Users\runneradmin\scoop\shims')
$want = 'chocolatey\bin'
@($dirs | Where-Object { $_ -like "*$want*" })
