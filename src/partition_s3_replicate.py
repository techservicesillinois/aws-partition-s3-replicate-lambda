"""
Replicate an S3 Object between AWS partitions, using IAM credentials. If the
source and destination buckets are in the same AWS partition then you should
use S3 Replication instead.

Features:

- Uses IAM User credentials to perform the replication.
- Uses an SQS FIFO Queue to serialize events for the same object. This
  processes events in the order they arrive to EventBridge, which may not be
  the same as the order they occurred.
- Stores metadata about the destination object in DynamoDB for future actions
  (such as deletion of versions).

This does not replication:

- Glacier Restores
- Lifecycle events (such as deletes). Use Lifecycle rules on the destination
  bucket directly.
- Intellegent Tiering. Use Intellegent Tiering on the destionation bucket
  directly.
"""
from datetime import datetime
import json
import logging
import os
from tempfile import TemporaryFile
from urllib.parse import urlencode

import boto3
from botocore.exceptions import ClientError

DST_BUCKET        = os.environ['DEST_BUCKET']
DST_BUCKET_REGION = os.environ['DEST_BUCKET_REGION']
DST_KMS_KEY       = os.environ.get('DEST_KMS_KEY')
DST_SECRET        = os.environ['DEST_SECRET']

OBJECTS_QUEUE = os.environ['OBJECTS_QUEUE']
OBJECTS_TABLE = os.environ['OBJECTS_TABLE']

LOGGING_LEVEL = getattr(
    logging,
    os.environ.get('LOGGING_LEVEL', 'INFO'),
    logging.INFO
)

logger = logging.getLogger(__name__)
logger.setLevel(LOGGING_LEVEL)

sm_clnt = boto3.client('secretsmanager')
sqs_rsrc = boto3.resource('sqs')

def get_dst_creds(secret_id=DST_SECRET):
    """
    Get the destination credentials from Secrets Manager. It expects the secret
    to have these fields: accesskey, secretkey, region.

    Args:
        secret_id (str): the name or ARN of the secret.
    """
    res = sm_clnt.get_secret_value(SecretId=secret_id)
    data = json.loads(res['SecretString'])

    creds = {
        'aws_access_key_id': data['accesskey'],
        'aws_secret_access_key': data['secretaccesskey'],
        'region_name': DST_BUCKET_REGION,
    }

    return creds

class ReplicateObject:
    """
    Instance to handle replicating a single object to the destination bucket,
    with metadata and tags.
    """
    _dst_creds = {}

    @classmethod
    def dst_creds(cls):
        """
        Get the destination credentials. This caches them as a class attribute
        to not keep looking them up in Secrets Manager each call.

        Returns:
            dict: credentials ready for use with a boto3.Session.
        """
        if not cls._dst_creds:
            cls._dst_creds = get_dst_creds()
        return cls._dst_creds

    def __init__(self, detail):
        self._detail = detail
        self._logger = logger.getChild(
            f"ReplicateObject({self.key}?versionId={self.version_id or ''})"
        )

        self._dst_session = boto3.Session(**self.dst_creds())
        self._dst_s3_clnt = self._dst_session.client('s3')
        self._dst_bucket = None

        self._src_session = boto3.Session()
        self._src_s3_clnt = self._src_session.client('s3')
        self._src_bucket = None

        self._objects_table = None

    @property
    def bucket_name(self):
        """ Get the bucket name (from the event). """
        return self._detail['bucket']['name']

    @property
    def dst_bucket(self):
        """ Get the destination Bucket resource. """
        if not self._dst_bucket:
            self._dst_bucket = self._dst_session.resource('s3').Bucket(DST_BUCKET)
        return self._dst_bucket

    @property
    def dst_object_curr(self):
        """ Get the destination object via head_object, always the most current. """
        obj = self._dst_s3_clnt.head_object(
            Bucket=DST_BUCKET,
            Key=self.key,
        )
        obj.setdefault('VersionId', None)
        obj.pop('ResponseMetadata', None)
        return obj

    @property
    def key(self):
        """ Get the object key. """
        return self._detail['object']['key']

    @property
    def logger(self):
        """ Get the logger instance. """
        return self._logger

    @property
    def object_item(self):
        """
        Get the destination object item, from the objects table.

        Returns:
            dict, dict: the DestObject and DestObjectTags fields.
        """
        item = self.objects_table.get_item(
            Key={
                'Key': self.key,
                'VersionId': (self.version_id or '$null')
            }
        ).get('Item', {})
        return item.get('DestObject', {}), item.get('DestObjectTags', {})

    @object_item.setter
    def object_item(self, value):
        """
        Set the destination object item, in the objects table. If passed a
        tuple of the obj data (from head_object) and tags then the item is
        updated. You can specify None for either of those to not change their
        values in the table.

        If passed None then the item is deleted.

        Args:
            value (None or (dict, dict)): the value to set.
        """
        if value is None:
            self.logger.debug('Deleting data from objects table')
            self.objects_table.delete_item(
                Key={
                    'Key': self.key,
                    'VersionId': (self.version_id or '$null')
                }
            )
        else:
            obj, tags = value
            set_exprs = []
            attr_names = {}
            attr_values = {}

            if obj is not None:
                for obj_key, obj_val in obj.items():
                    if isinstance(obj_val, datetime):
                        obj[obj_key] = obj_val.isoformat()
                set_exprs.append('#DO = :obj')
                attr_names['#DO'] = 'DestObject'
                attr_values[':obj'] = obj

            if tags is not None:
                if not isinstance(tags, dict):
                    tags = {t['Key']: t['Value'] for t in tags}
                set_exprs.append('#DOT = :tags')
                attr_names['#DOT'] = 'DestObjectTags'
                attr_values[':tags'] = tags

            if not set_exprs:
                return

            self.logger.debug(
                'Writing data to objects table: obj=%(obj)r; tags=%(tags)r',
                {'obj': obj, 'tags': tags}
            )
            self.objects_table.update_item(
                Key={
                    'Key': self.key,
                    'VersionId': (self.version_id or '$null'),
                },
                UpdateExpression="SET " + ', '.join(set_exprs),
                ExpressionAttributeNames=attr_names,
                ExpressionAttributeValues=attr_values,
            )

    @property
    def objects_table(self):
        """ Get the objects Table resource. """
        if not self._objects_table:
            self._objects_table = self._src_session.resource('dynamodb').Table(OBJECTS_TABLE)
        return self._objects_table

    @property
    def src_bucket(self):
        """ Get the source Bucket resource. """
        if not self._src_bucket:
            self._src_bucket = self._src_session.resource('s3').Bucket(
                self._detail['bucket']['name']
            )
        return self._src_bucket

    @property
    def src_object(self):
        """ Get the source object, via head_object. """
        params = {
            'Bucket': self.bucket_name,
            'Key': self.key,
        }
        if self.version_id:
            params['VersionId'] = self.version_id

        obj = self._src_s3_clnt.head_object(**params)
        obj.setdefault('VersionId', None)
        obj.pop('ResponseMetadata', None)
        return obj

    @property
    def src_object_tags(self):
        """ Get the source object tags, as a dict. """
        params = {
            'Bucket': self.bucket_name,
            'Key': self.key,
        }
        if self.version_id:
            params['VersionId'] = self.version_id

        res = self._src_s3_clnt.get_object_tagging(**params)
        return {t['Key']: t['Value'] for t in res.get('TagSet', [])}

    @property
    def version_id(self):
        """ Get the object version-id. """
        return self._detail['object'].get('version-id')

    def handle_created(self):
        """
        Handle an event where an object was created in the source bucket. This
        copies it to the destination bucket and stores its ID in the table.
        """
        src_object, src_object_tags = self.src_object, self.src_object_tags

        with TemporaryFile('w+b') as temp_fh:
            src_extra_args = {}
            if self.version_id:
                src_extra_args['VersionId'] = self.version_id

            self.logger.debug('Downloading object')
            self.src_bucket.download_fileobj(
                Fileobj=temp_fh,
                Key=self.key,
                ExtraArgs=src_extra_args,
            )

            dst_extra_args = {}
            for name in [
                    'CacheControl', 'Expires',
                    'ContentDisposition', 'ContentEncoding', 'ContentLanguage', 'ContentType',
                    'Metadata',
                ]:
                if src_object.get(name):
                    dst_extra_args[name] = src_object[name]
            if src_object_tags:
                dst_extra_args['Tagging'] = urlencode(src_object_tags)
            if DST_KMS_KEY:
                dst_extra_args.update(
                    ServerSideEncryption='aws:kms',
                    SSEKMSKeyId=DST_KMS_KEY,
                )

            self.logger.debug(
                'Uploading object: ExtraArgs=%(extra_args)r',
                {'extra_args': dst_extra_args}
            )
            temp_fh.seek(0, os.SEEK_SET)
            self.dst_bucket.upload_fileobj(
                Fileobj=temp_fh,
                Key=self.key,
                ExtraArgs=dst_extra_args,
            )

            dst_object = self.dst_object_curr
            self.logger.info(
                'Uploaded object: VersionId=%(ver)s',
                {'ver': dst_object['VersionId']}
            )

            self.object_item = dst_object, src_object_tags

    def handle_deleted(self):
        """
        Handle an event where an object was deleted in the source bucket. This
        deletes it in the destination bucket and updates its data in the table.
        """
        dst_item, _ = self.object_item
        if not dst_item:
            self.logger.error('Not found in the objects table')
            return

        dst_obj_ver = dst_item.get('VersionId')
        if not dst_obj_ver and self.version_id:
            self.logger.error('Corrupt item in the objects table: no DestObject.VersionId')
            return

        dst_obj_url = f"s3://{DST_BUCKET}/{self.key}?versionId={dst_obj_ver}"
        try:
            self.logger.debug('Deleting object: VersionId=%(ver)s', {'ver': dst_obj_ver})
            params = {
                'Bucket': DST_BUCKET,
                'Key': self.key,
            }
            if dst_obj_ver:
                params['VersionId'] = dst_obj_ver
            self._dst_s3_clnt.delete_object(**params)
        except ClientError as client_err:
            if client_err.response['Error']['Code'] not in ('404', 'NotFound'):
                raise
            self.logger.warning('Object already deleted: %(obj)s', {'obj': dst_obj_url})
        else:
            self.logger.info('Deleted object: %(obj)s', {'obj': dst_obj_url})

        self.object_item = None

    def handle_tags(self):
        """
        Handle an event where an objects tags were modified in the source
        bucket. This will sync those tags to the destination bucket.
        """
        tags = self.src_object_tags
        tagset = [{'Key': k, 'Value': v} for k, v in tags.items()]

        dst_item, _ = self.object_item
        if not dst_item:
            self.logger.error('Not found in the objects table')
            return

        dst_obj_ver = dst_item.get('VersionId')
        if not dst_obj_ver and self.version_id:
            self.logger.error('Corrupt item in the objects table: no DestObject.VersionId')
            return

        self.logger.debug(
            'Setting destination object tags: %(tagset)r',
            {'tagset': tagset}
        )
        params = {
            'Bucket': DST_BUCKET,
            'Key': self.key,
        }
        if dst_obj_ver:
            params['VersionId'] = dst_obj_ver
        if tags:
            self._dst_s3_clnt.put_object_tagging(
                Tagging={'TagSet': tagset},
                **params
            )
        else:
            self._dst_s3_clnt.delete_object_tagging(**params)

        self.object_item = None, tags

def event_handler(event, context):
    """
    Take an S3 object event, determine if we should process it, and if so put
    it in the SQS FIFO Queue. This will ignore object restoration and lifecycle
    events.

    Args:
        event (dict): S3 object event.
        context (obj): Lambda context.
    """
    # pylint: disable=unused-argument
    obj_key = event['detail']['object']['key']
    obj_ver = event['detail']['object'].get('version-id', '')
    obj_logger = logger.getChild(f"Object({obj_key}?versionId={obj_ver})")
    obj_logger.debug(
        'Handling event: %(event)r',
        {'event': event}
    )

    detail_type = event['detail-type']
    detail = event['detail']

    if detail_type not in {
            'Object Created', 'Object Deleted',
            'Object Tags Added', 'Object Tags Deleted'
        }:
        obj_logger.debug('Skipping: %(type)s', {'type': detail_type})
        return
    if detail_type == 'Object Deleted' and detail.get('reason') != 'DeleteObject':
        obj_logger.debug(
            'Skipping %(type)s (%(reason)s)',
            {'type': detail_type, 'reason': detail.get('reason', '(unknown)')}
        )
        return

    queue = sqs_rsrc.Queue(OBJECTS_QUEUE)
    res = queue.send_message(
        MessageBody=json.dumps(event),
        MessageGroupId=detail['object']['key'],
    )

    obj_logger.info(
        'Queued event %(type)s (%(reason)s): %(msg_id)s',
        {
            'type': detail_type,
            'reason': detail.get('reason', '(unknown)'),
            'msg_id': res['MessageId'],
        }
    )

def queue_handler(event, context):
    """
    Take records from the SQS FIFO Queue for objects and do the object
    replication.

    Args:
        event (dict): SQS records of events.
        context (obj): Lambda context.
    """
    # pylint: disable=unused-argument
    failures = []
    for record in event['Records']:
        try:
            record_event = json.loads(record['body'])
        except json.JSONDecodeError:
            logger.exception(
                'Unable to decode record body: %(body)s',
                {'body': record['body']}
            )
            continue

        try:
            replicate_object = ReplicateObject(detail=record_event['detail'])
            replicate_object.logger.debug(
                'Processing record event: %(event)r',
                {'event': record_event}
            )

            record_detail_type = record_event['detail-type']
            if record_detail_type == 'Object Created':
                replicate_object.handle_created()
            elif record_detail_type == 'Object Deleted':
                replicate_object.handle_deleted()
            elif record_detail_type in {'Object Tags Added', 'Object Tags Deleted'}:
                replicate_object.handle_tags()
            else:
                replicate_object.logger.error(
                    'Unknown record event detail type: %(type)s',
                    {'type': record_detail_type}
                )
        except Exception: # pylint: disable=broad-except
            logger.exception('Unable to process record event: %(event)r', {'event': record_event})
            failures.append({
                'itemIdentifier': record['messageId']
            })

    return { "batchItemFailures": failures }
