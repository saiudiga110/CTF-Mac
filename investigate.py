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

# 1. Check S3 buckets
print("=" * 60)
print("S3 BUCKETS")
print("=" * 60)
s3 = get_client('s3')
try:
    buckets = s3.list_buckets()
    for b in buckets.get('Buckets', []):
        print(f"  Bucket: {b['Name']}")
except Exception as e:
    print(f"  Error: {e}")

# 2. List objects in each bucket
print("\n" + "=" * 60)
print("S3 BUCKET CONTENTS")
print("=" * 60)
try:
    for b in buckets.get('Buckets', []):
        bname = b['Name']
        print(f"\n--- Bucket: {bname} ---")
        try:
            paginator = s3.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=bname):
                for obj in page.get('Contents', []):
                    print(f"  {obj['Key']}  (Size: {obj['Size']}, Modified: {obj['LastModified']})")
        except Exception as e:
            print(f"  Error listing: {e}")
except Exception as e:
    print(f"  Error: {e}")

# 3. CloudTrail - lookup ALL events with pagination
print("\n" + "=" * 60)
print("CLOUDTRAIL EVENTS (ALL)")
print("=" * 60)
ct = get_client('cloudtrail')
try:
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
        if not next_token or len(all_events) > 500:
            break
    
    # Filter out our own LookupEvents calls and show unique events
    for evt in all_events:
        event_name = evt.get('EventName')
        username = evt.get('Username')
        # Show events that are NOT our own lookups
        if event_name != 'LookupEvents' or username != 'eastreach-investigator':
            print(f"\n  EventId: {evt.get('EventId')}")
            print(f"  EventName: {event_name}")
            print(f"  EventTime: {evt.get('EventTime')}")
            print(f"  Username: {username}")
            print(f"  EventSource: {evt.get('EventSource')}")
            if 'CloudTrailEvent' in evt:
                ct_event = json.loads(evt['CloudTrailEvent'])
                print(f"  CloudTrailEvent: {json.dumps(ct_event, indent=4)}")
    
    print(f"\n  Total events found: {len(all_events)}")
    # Count unique event names
    event_names = {}
    for evt in all_events:
        name = evt.get('EventName', 'unknown')
        user = evt.get('Username', 'unknown')
        key = f"{name} by {user}"
        event_names[key] = event_names.get(key, 0) + 1
    print("\n  Event summary:")
    for k, v in sorted(event_names.items()):
        print(f"    {k}: {v}")
        
except Exception as e:
    print(f"  Error: {e}")
