import boto3
import json

endpoint_url = "http://154.57.164.66:31635"
region = "us-east-1"
access_key = "AKIARNOERVM2HVDH8Y1X"
secret_key = "0EzPYr8mNZJ/DZLMAD/x8M9GpNKFMo6n8paRqVG4"

def get_client(service):
    return boto3.client(
        service,
        endpoint_url=endpoint_url,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key
    )

# Focus on interesting events (not LookupEvents, not DescribeTrails, etc.)
INTERESTING_EVENTS = [
    'PutObject', 'GetObject', 'SendMessage', 'PutItem', 
    'Publish', 'PurgeQueue', 'ReceiveMessage', 'AssumeRole',
    'CreateAccessKey', 'DeleteAccessKey', 'Decrypt',
    'GetSecretValue', 'GetQueueAttributes', 'POSTRequest',
    'StopLogging', 'StartLogging', 'CreateTrail', 'GetTrailStatus',
    'Unknown', 'ListSecrets'
]

ct = get_client('cloudtrail')
all_events = []
next_token = None
while True:
    kwargs = {'MaxResults': 50}
    if next_token:
        kwargs['NextToken'] = next_token
    response = ct.lookup_events(**kwargs)
    events = response.get('Events', [])
    all_events.extend(events)
    next_token = response.get('NextToken')
    if not next_token or len(all_events) > 600:
        break

print("=" * 80)
print("INTERESTING EVENTS (sorted by time)")
print("=" * 80)

interesting = []
for evt in all_events:
    event_name = evt.get('EventName')
    if event_name in INTERESTING_EVENTS:
        interesting.append(evt)

# Sort by time
interesting.sort(key=lambda x: x.get('EventTime', ''))

for evt in interesting:
    print(f"\n{'='*80}")
    print(f"  EventName: {evt.get('EventName')}")
    print(f"  EventTime: {evt.get('EventTime')}")
    print(f"  Username: {evt.get('Username')}")
    print(f"  EventSource: {evt.get('EventSource')}")
    if 'CloudTrailEvent' in evt:
        ct_event = json.loads(evt['CloudTrailEvent'])
        print(f"  Full Event: {json.dumps(ct_event, indent=4)}")
