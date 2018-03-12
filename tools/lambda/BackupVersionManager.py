import json
import urllib.parse
import boto3

from datetime import datetime, timedelta

print('Loading function')

s3 = boto3.client('s3')
cloudwatch = boto3.client('cloudwatch')

# First backup of the year is tagged with "keep-days=[0]" where [0] is the zero
# index of the matching array below.  First backup of the quarter is index 1, 
# and so on as we get more and more frequent
# [0] = yearly
# [1] = quarterly
# [2] = monthly
# [3] = weekly
# [4] = daily
# [5] = all others
tier_mapping = {
    'documents': [1095, 365, 90, 30, 7, 3],
    '__default': [3650, 730, 365, 90, 30, 15]
}

archive_bucket = 'com.mysite.myarchive.bucket'
metric_namespace = 'SimpleS3BackupManager'
debug = False

def parse_key(key):
    s1 = key.split('/')
    s2 = s1[1].split('_')
    archive_group = '.'.join(s2[1].split('.')[:-1])
    date_split = s1[0].split('-')
    time_split = s2[0].split('-')
    date = datetime(int(date_split[0]), int(date_split[1]), int(date_split[2]), int(time_split[0]), int(time_split[1]), int(time_split[2]))
    
    return (archive_group, date)

def key_to_archive_group(key):
    return '.'.join(key.split('.')[:-1])
    
class ArchiveBucket(object):
    __borg = {}
    
    def __init__(self, bucket_name):
        self.bucket_name = bucket_name
    
    def list_objects(self):
        if self.bucket_name in self.__borg:
            return self.__borg[self.bucket_name]
        
        # List bucket
        bucket = boto3.resource('s3').Bucket(self.bucket_name)
        if debug:
            print('Getting object list from bucket {}'.format(self.bucket_name))
        try:
            listing = bucket.objects.all()
        except Exception as e:
            print(e)
            print('Error fetching object listing for bucket {}.'.format(self.bucket_name))
            raise e
        
        self.__borg[self.bucket_name] = listing
        return self.__borg[self.bucket_name]
    
    def get_archive_groups(self):
        return list(set(map(lambda x: parse_key(x.key)[0], self.list_objects())))

    def get_archival_tag(self, key):
        if debug:
            print('Getting archival tag for object s3://{}/{}'.format(self.bucket_name, key))
        try:
            response = s3.get_object_tagging(
                Bucket=self.bucket_name,
                Key=key
            )
            keep_days_tag = list(filter(lambda x: x['Key'] == 'keep-days', response['TagSet']))
            if len(keep_days_tag) > 0:
                return int(keep_days_tag[0]['Value'])
            else:
                return None
        except Exception as e:
            print(e)
            print('Error fetching archival tag (keep-days) from key {} in bucket {}.'.format(key, self.bucket_name))
            return None

    def apply_archival_tag(self, key, days):
        print('Applying tag keep-days:{} to {} in bucket {}'.format(str(days), key, self.bucket_name))
        try:
            response = s3.put_object_tagging(
                Bucket=self.bucket_name,
                Key=key,
                Tagging={
                    'TagSet':[
                        {
                            'Key': 'keep-days',
                            'Value': str(days)
                        }
                    ]
                }
            )
        except Exception as e:
            print(e)
            print('Error applying archival tag (keep-days) of {} to key {} in bucket {}.'.format(str(days), key, self.bucket_name))
            raise e

    # This method is no longer used
    def copy_to_archives(self, source_bucket, source_key, archive_date, keep_days):
        archive_key = self.build_archive_key(source_key, archive_date)
        try:
            s3.copy_object(
                Bucket=self.bucket_name,
                Key=archive_key,
                CopySource={'Bucket': source_bucket, 'Key': source_key},
                TaggingDirective='REPLACE',
                Tagging='keep-days={}&creation-date={}'.format(keep_days, archive_date.isoformat())
            )
            print('Copied backup s3://{}/{} to archive location s3://{}/{}'.format(source_bucket, source_key, self.bucket_name, archive_key))
        except Exception as e:
            print(e)
            print('Error attempting to copy s3://{}/{} to s3://{}/{}.'.format(source_bucket, source_key, self.bucket_name, archive_key))
            raise e
    
    def build_archive_key(self, key, date):
        return "{:04}-{:02}-{:02}/{:02}-{:02}-{:02}_{}".format(date.year, date.month, date.day, date.hour, date.minute, date.second, key)

class ArchiveCollection(object):
    bucket = None
    archive_name = None
    
    _archives = None
    _key_tier_map = None
    _date_part_map = None
    _archives_by_date = None
    
    __borg = {}
    
    # Optionally specify a listing if we've already queried it this go around
    # TODO: listing should be passed more elegantly.  Borg model perhaps?
    def __init__(self, bucket, archive_name, listing=None):
        self.bucket = bucket
        self.archive_name = archive_name
        if listing is not None and 'listing' not in self.__borg:
            self.__borg['listing'] = listing
    
    def get_archives(self):
        if self._archives is not None:
            return self._archives
        
        self._archives = list(filter(lambda x: parse_key(x.key)[0] == self.archive_name, self.bucket.list_objects()))
        return self._archives
    
    # This function only works if ALL dates are fed to it IN SORTED ORDER.
    # It will update the mapping with each subsequent date that is fed in.
    def __determine_archive_tier(self, date, mapping, persist=True):
        def get_next_map(given_map, newkey):
            found = True
            returnmap = {}
            if newkey in given_map:
                returnmap = given_map[newkey]
            else:
                found = False
                if persist:
                    given_map[newkey] = returnmap
                
            return found, returnmap
        
        # Get year, quarter, month, week, day, and second
        date_parts = [date.year, (date.month - 1) / 3, date.month, date.isocalendar()[1], date.day, (date.hour*60*60)+(date.minute*60)+date.second]
        
        # Loop over date parts and find our tier
        tier = 0
        current_map = mapping
        for part in date_parts:
            found, current_map = get_next_map(current_map, part)
            if found:
                tier = tier + 1

        if debug:
            print("__determine_archive_tier({}, {}):: ({}) {}".format(str(date), str(mapping), str(tier), str(date_parts)))
        return tier
    
    # Map of archive key names to their tier number
    def __get_key_tier_map(self):
        if self._key_tier_map is not None:
            return self._key_tier_map
        
        key_tier_map = {}
        date_part_map = self.__get_date_part_map()
        date_archive_map = self.get_archives_by_date()
        
        for date in sorted(date_archive_map.keys()):
            archive_info = date_archive_map[date]
            key_tier_map[archive_info['key']] = self.__determine_archive_tier(date, date_part_map)
        
        self._key_tier_map = key_tier_map
        return self._key_tier_map
        
    # Map of date parts
    def __get_date_part_map(self):
        if self._date_part_map is None:
            self._date_part_map = {}
        return self._date_part_map
    
    def __parse_key_date_mapper(self, obj):
        key = obj
        if hasattr(obj, 'key'):
            key = obj.key
        name, date = parse_key(key)
        return (date, {'name': name, 'key': key})
    
    def get_archives_by_date(self):
        if self._archives_by_date is None:
            self._archives_by_date = dict(map(self.__parse_key_date_mapper, self.get_archives()))
        return self._archives_by_date
    
    def determine_archive_tier(self, key):
        if debug:
            print("determine_archive_tier(\"{}\") :: {}".format(key, str(self.__get_key_tier_map())))
        tier_map = self.__get_key_tier_map()
        return tier_map[key]
    
    def determine_archive_retention(self, key):
        tier = self.determine_archive_tier(key)
        return self.get_tier_mapping()[tier]
    
    def determine_date_archive_tier(self, date):
        # Populate the tier map first
        self.__get_key_tier_map()
        tier = self.__determine_archive_tier(date, self.__get_date_part_map(), False)
        return tier
    
    def get_tier_mapping(self):
        return tier_mapping.get(self.archive_name, tier_mapping.get('__default'))
    
    def fix_tags(self):
        corrected_tags_count = 0
        archives = list(map(lambda x: x.key, self.get_archives()))
        for archive in archives:
            expected_retention_days = self.determine_archive_retention(archive)
            retention_days = self.bucket.get_archival_tag(archive)
            
            print ("Found {} with current retention tag of {} and expected tag of {}".format(archive, retention_days, expected_retention_days))
            if retention_days is None or retention_days < expected_retention_days:
                self.bucket.apply_archival_tag(archive, expected_retention_days)
                corrected_tags_count = corrected_tags_count + 1
        
        return corrected_tags_count
    
    # For an archive that already exists in the archival bucket but we know was
    # only uploaded recently.  Tag the archive appropriately.
    def tag_new_archive(self, key):
        # Determine how many days of retention this archive should get
        retention_days = self.determine_archive_retention(key)
        print("Archive {} will be retained {} days".format(key, str(retention_days)))
        
        # Apply tag
        self.bucket.apply_archival_tag(key, retention_days)

class Metric:
    metric = None
    dimensions = None
    unit = None
    value = None
    
    def __init__(self, metric, dimensions, unit, timestamp=None):
        self.metric = metric
        self.dimensions = dimensions
        self.unit = unit
        self.timestamp = timestamp or datetime.now()
    
    def get_data(self):
        return {
            'MetricName': self.metric,
            'Dimensions': self.dimensions,
            'Timestamp': self.timestamp,
            'Value': self.value,
            'Unit': self.unit
        }
    
class MetricManager:
    cloudwatch = None
    __metrics = {}
    
    def __init__(self):
        self.cloudwatch = boto3.client('cloudwatch')
    
    def add_metric(self, key, metric, value, unit='None', dimensions=[]):
        if key not in self.__metrics:
            self.__metrics[key] = Metric(metric, dimensions, unit)
        
        metric = self.__metrics[key]
        metric.value = value
    
    def __get_metric_data(self):
        return list(map(lambda x: x.get_data(), self.__metrics.values()))
    
    # post metrics to cloudwatch
    def post_metrics(self, pretend=False):
        data = self.__get_metric_data()
        try:
            if not pretend:
                cloudwatch.put_metric_data(
                    Namespace=metric_namespace,
                    MetricData=data
                )
                print('Posted metric data {} to Cloudwatch'.format(str(data)))
            else:
                print('DID NOT post metric data {} to Cloudwatch because pretend mode was enabled'.format(str(data)))
        except Exception as e:
            print(e)
            print('Error posting Cloudwatch metric data {}'.format(str(data)))
    
    def __getitem__(self, key):
        return self.__metrics[key]

def handler(event, context):
    global debug
    if debug:
        print("Received event: " + json.dumps(event, indent=2))

    disable_metric_posting = False
    if 'testmode' in event:
        print('This appears to be a test execution')
        disable_metric_posting = True
        debug = True
    
    metrics = MetricManager()
    if 'Records' in event:
        handle_uploaded_archive(event, context)
        metrics.post_metrics(disable_metric_posting)
    elif (('source' in event) and (event['source'] == 'aws.events')):
        handle_scheduled_event(event, context)
        metrics.post_metrics(disable_metric_posting)
    else:
        print('Error, event type not recognized!')
        print("Received event: " + json.dumps(event, indent=2))

def handle_uploaded_archive(event, context):
    # Get the object from the event and show its content type
    bucket_name = event['Records'][0]['s3']['bucket']['name']
    key = urllib.parse.unquote_plus(event['Records'][0]['s3']['object']['key'], encoding='utf-8')
    print("Bucket: {}, Key: {}".format(bucket_name, key))
    
    bucket = ArchiveBucket(bucket_name)
    archive_group, archive_date = parse_key(key)
    archives = ArchiveCollection(bucket, archive_group)
    
    # Apply retention tag
    archives.tag_new_archive(key)
    
    # Post metric
    metrics = MetricManager()
    metrics.add_metric('TaggedNewArchive_{}'.format(archive_group), 'TaggedNewArchive', 1, dimensions=[{'Name':'ArchiveGroup', 'Value': archive_group}])
    
def handle_scheduled_event(event, context):
    metrics = MetricManager()
    bucket = ArchiveBucket(archive_bucket)
    archive_groups = bucket.get_archive_groups()
    print("Found archive groups: {}".format(str(archive_groups)))
    
    corrected_tags_count = 0
    for group in archive_groups:
        archive_collection = ArchiveCollection(bucket, group)
        fixed = archive_collection.fix_tags()
        corrected_tags_count = corrected_tags_count + fixed
    
    print("Completed tag check operation.  Attempted to fix {} missing tags.".format(str(corrected_tags_count)))
    metrics.add_metric('FixedTags', 'FixedTags', corrected_tags_count, 'Count')
