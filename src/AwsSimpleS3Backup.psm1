function Backup-FilesToS3 {
    <#
    .SYNOPSIS
        Creates backups, uploads them to AWS S3, and retains some locally

    .DESCRIPTION
        This powershell module is designed to make creation of backups simple
        and easy and to allow for easy uploading of those backups to S3 for
        long-term archival and disaster recovery.
        
        Author: Nathan Przybyszewski <github.com/shibz>
        License: MIT License
        
        Before using this script, you need to create your backup_settings.txt
        file using the included template. You also should run the following
        command as administrator to set up logging:
        
        New-EventLog -LogName Application -Source "YourBackupSourceName"
        
        Replace YourBackupSourceName with the actual setting you configured
        in backup_settings.txt
    
    .PARAMETER name
        Name for the backups.  Date/time will be appended.
    
    .PARAMETER source
        Location to backup.
    
    .PARAMETER cfgFile
        Configuration file that will be used to deremine where to backup to.
    
    .EXAMPLE
        Backup files locally and to S3 as specified by configuration file.
        
        Backup-FilesToS3 "documents" "C:\Users\Administrator\My Documents\*" "C:\backups\backup_settings.txt"
    #>
    Param(
        [Parameter(Mandatory=$true)][string]$name,
        [Parameter(Mandatory=$true)][string]$source,
        [Parameter(Mandatory=$true)][string]$cfgFile
    )

    # Ensure 7-zip is installed and the necessary backup settings file exists
    if (-not (test-path "$env:ProgramFiles\7-Zip\7z.exe")) {throw "$env:ProgramFiles\7-Zip\7z.exe needed"}
    if (-not (test-path "$cfgFile")) {throw "$cfgFile was not found.  Copy backup_settings.template.txt and configure as desired.  Location of your configuration must be passed to cmdlet as a parameter."}

    # Fetch backup config file content and populate $backupSettings with values
    Get-Content "$cfgFile" | foreach-object -begin {$backupSettings=@{}} -process { $k = [regex]::split($_,'='); if(($k[0].CompareTo("") -ne 0) -and ($k[0].StartsWith("[") -ne $True) -and ($k[0].StartsWith("#") -ne $True)) { $backupSettings.Add($k[0], $k[1]) } }

    # Set the logging-related variables
    $script:logSource = $backupSettings.LogSource
    $script:debug = $backupSettings.Debug

    # Set the AWS credentials
    Set-AWSCredential -AccessKey "$($backupSettings.AccessKey)" -SecretKey "$($backupSettings.SecretKey)"
    Set-DefaultAWSRegion -Region "$($backupSettings.S3Region)"

    $now = Get-Date
    $timestamp = Get-Date -Date $now -format yyyy-MM-dd_H-mm-ss
    $backupDir = "$($backupSettings.LocalBackupDir)\$name"
    $backupName = "$backupDir\$name`_$timestamp.bak.7z"
    $cloudUpload = $backupSettings.CloudUpload -like "true"
    Write-BackupLog "Beginning backup for $name to $backupName.`r`nWill attempt to save:`r`n$source" 110

    if ($source -is [array]) {
        $szoutput = Zip-Files "$backupName" @source
    } else {
        $szoutput = Zip-Files "$backupName" $source
    }
    Write-BackupDebug "7Zip completed.  Output was:`r`n$szoutput" 120

    if ($cloudUpload) {
        $uploadlog = Upload-Backup $backupName $backupSettings.S3Bucket "$name.7z" $now
    } else {
        $uploadlog = "Backup was not uploaded to S3"
    }

    $backupMax = $now.AddDays([int]$backupSettings.LocalBackupRetention * -1)
    $oldfiles = Get-ChildItem $backupdir -recurse -include "$backupname_*.bak.7z" | Where-Object { $_.CreationTime -le $backupMax }
    if ($oldfiles) {
        $measurement = $oldfiles | Measure-Object -property length -sum
        $pruneCount = $measurement.Count
        $pruneSize = [math]::Round($measurement.Sum / 1KB)
        $prunelog = "Pruning old archives.`r`nWill attempt to prune $pruneCount files totalling $pruneSize KB:`r`n$oldfiles`r`n$uploadlog"
        Write-BackupDebug $prunelog 140
        Remove-Item $oldfiles
    } else {
        $prunelog = "No files found for pruning"
    }

    Write-BackupLog "Backup for $name to $backupName is complete!`r`nBackup target was: $source`r`n`r`n7Zip output was:`r`n$szoutput`r`n$prunelog`r`n$backuplog" 150
}

# Create an encrypted 7-zip archive
function Zip-Files {
    "$env:ProgramFiles\7-Zip\7z.exe" a -mx7 -mhe -mmt -p"$($backupSettings.EncryptionPassword)" $args
}

# Write to event log, only if debug mode is enabled
function Write-BackupDebug($message, $id) {
    if ($script:debug) {
        Write-EventLog -LogName Application -Source $script:logSource -EventId "$id" -Message "$message"
        echo $message
    }
}

# Write informational message to event log
function Write-BackupLog($message, $id) {
    Write-EventLog -LogName Application -Source $script:logSource -EventId "$id" -Message "$message"
    echo $message
}

# Write error message to event log
function Write-BackupError($message, $id) {
    Write-EventLog -LogName Application -Source $script:logSource -EventId "$id" -Message "$message" -EntryType Error
    echo $message
}

# Upload the given file to S3
function Upload-Backup($file, $bucket, $key, $date) {
    $prefixDate = Get-Date -Date $date -format yyyy-MM-dd/HH-mm-ss
    $archiveKey = "$prefixDate`_$key"
    $backupLog = ""

    Write-BackupDebug "Attempting to upload $file to s3://$bucket/$archiveKey" 135
    try {
        Write-S3Object -BucketName $bucket -File $file -Key $archiveKey
        $backupLog = "Uploaded $file to s3://$bucket/$archiveKey"
    } catch {
        $errormsg = $_.Exception.Message
        Write-BackupError "Error uploading to S3!  Error was:`r`n$errormsg" 235
    }
    Write-BackupDebug $backupLog 130

    return $backupLog
}

Export-ModuleMember -Function Backup-FilesToS3
