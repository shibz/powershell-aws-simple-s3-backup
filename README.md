# PowerShell AWS S3 Simple Backup
This is a PowerShell module for performing simple backups to S3.  Also included is a Lambda function for managing the uploaded backups.  The Lambda function will age-out old backups while still retaining certain backups (ex. first backup of each year, of each quarter, of each month, etc) for a specified amount of time.  Lambda function also posts to CloudWatch for each newly uploaded backup so that an alarm can be created to alert if backups stop.

# End-host Configuration
1. Download script from Github and install as desired.  In this example we'll
   keep it simple by saving the entire directory to C:\backups.
2. Copy backup_settings.template.txt file, rename it, and edit it as desired.
3. Create backup script.  Script can be executed as a Windows Scheduled Task.  Depending what you're attempting to backup, you may need to add additional commands to your script.  For example, if you're backing up a MySQL database, you'll want to use mysqldump to export the database before backing up the resulting file to S3.

```
Import-Module C:\backups\powershell-aws-simple-s3-backup\AwsSimpleS3Backup.psd1

# First argument is the backup name.
# Second argument is the location to backup files from
# Third argument is the settings file that will be used to determine backup behavior
Backup-FilesToS3 "documents" "C:\Users\Administrator\My Documents\*" "C:\backups\powershell-aws-simple-s3-backup\backup_settings.txt"
```

# Lambda Installation
1. Upload script from tools/lambda directory to your AWS account's Lambda console as a Python 3.x script.
2. See Lambda Example IAM Role Policy below for an example policy that you can use for this script.
3. Set up Lambda to call this function upon S3 "ObjectCreated" events from your S3 bucket.
4. If desired, configure CloudWatch alarms to alert you if backups stop arriving.  You will need to wait until after the first execution before the metric appears.  Backups must occur at least once per day for this to be effective.
5. Currently, S3 Lifecycle Policies must be configured manually for pruning to work.  The Lambda script will tag each archive with the "keep-days" tag and a value indicating the number of days that the archive should be retained.  For each possible retention period, you must create the desired lifecycle policies.  You can create policies to delete the objects, or for longer-lived objects transition them to Glacier then eventually delete them.  For example:
* keep-days=7: Expire current version of object after 7 days from object creation
* keep-days=356: Transition to Amazon Clacier after 15 days from object creation, expire current version of object after 365 days from object creation.

# Lambda Configuration
Edit the following variables in the Lambda script to suit your requirements.

archive_bucket: Name of the S3 bucket that you are uploading backups to

metric_namespace: Namespace to use when posting to CloudWatch.  Can usually just be left at the default unless you prefer something else.

tier_mapping: should be modified with your desired retention periods.  The __default mapping applies to all uploaded backups unless overridden by another.  The number associated with each interval indicates how many days the first backup of each interval will be retained.  For example, imagine a backup is uploaded once per week on the following days: Jan 1, Jan 8, Jan 15, Jan 22, Jan 29, Feb 5, Feb 12.  The Jan 1 backup will be retained for 3650 days because it is the first backup of the year.  The Feb 5 backup will be retained for 365 days because it is the first backup of the month of February.  The other backups in this example will be retained only 15 days.

```
[3650, 730, 365, 90, 30, 15]
  |     |    |   |   |   |
  |     |    |   |   |   |- Others
  |     |    |   |   |- Daily
  |     |    |   |- Weekly
  |     |    |- Monthly
  |     |- Quarterly
  |- Yearly
```

# Lambda Example IAM Role Policy
```
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "Stmt1507002786000",
            "Effect": "Allow",
            "Action": [
                "s3:DeleteObject",
                "s3:PutObjectTagging",
                "s3:GetObjectTagging",
                "s3:PutObject",
                "s3:CopyObject",
                "s3:ListBucket"
            ],
            "Resource": [
                "arn:aws:s3:::my.archive.bucket",
                "arn:aws:s3:::my.archive.bucket/*"
            ]
        },
        {
            "Sid": "Stmt1507097378000",
            "Effect": "Allow",
            "Action": [
                "cloudwatch:PutMetricData"
            ],
            "Resource": [
                "*"
            ]
        }
    ]
}
```

# Credits
Powershell and Python script originally authored by Nathan Przybyszewski
[powershell-script-module-boilerplate](https://github.com/jpoehls/powershell-script-module-boilerplate) used as a template
