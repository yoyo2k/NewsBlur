import datetime
import mongoengine as mongo
import urllib2
from django.db.models import Avg, Count
from apps.rss_feeds.models import MFeedFetchHistory, MPageFetchHistory, MFeedPushHistory
from apps.rss_feeds.models import FeedLoadtime
from apps.social.models import MSharedStory
from apps.profile.models import Profile
from utils import json_functions as json


class MStatistics(mongo.Document):
    key   = mongo.StringField(unique=True)
    value = mongo.DynamicField()
    
    meta = {
        'collection': 'statistics',
        'allow_inheritance': False,
        'indexes': ['key'],
    }
    
    def __unicode__(self):
        return "%s: %s" % (self.key, self.value)
    
    @classmethod
    def get(cls, key, default=None):
        obj = cls.objects.filter(key=key).first()
        if not obj:
            return default
        return obj.value

    @classmethod
    def set(cls, key, value):
        obj, _ = cls.objects.get_or_create(key=key)
        obj.value = value
        obj.save()
    
    @classmethod
    def all(cls):
        stats = cls.objects.all()
        values = dict([(stat.key, stat.value) for stat in stats])
        for key, value in values.items():
            if key in ('avg_time_taken', 'sites_loaded', 'stories_shared'):
                values[key] = json.decode(value)
            elif key in ('feeds_fetched', 'premium_users', 'standard_users', 'latest_sites_loaded',
                         'max_sites_loaded', 'max_stories_shared'):
                values[key] = int(value)
            elif key in ('latest_avg_time_taken', 'max_avg_time_taken'):
                values[key] = float(value)
        
        values['total_sites_loaded'] = sum(values['sites_loaded']) if 'sites_loaded' in values else 0
        values['total_stories_shared'] = sum(values['stories_shared']) if 'stories_shared' in values else 0

        return values
        
    @classmethod
    def collect_statistics(cls):
        now = datetime.datetime.now()
        last_day = datetime.datetime.now() - datetime.timedelta(hours=24)
        cls.collect_statistics_feeds_fetched(last_day)
        print "Feeds Fetched: %s" % (datetime.datetime.now() - now)
        cls.collect_statistics_premium_users(last_day)
        print "Premiums: %s" % (datetime.datetime.now() - now)
        cls.collect_statistics_standard_users(last_day)
        print "Standard users: %s" % (datetime.datetime.now() - now)
        cls.collect_statistics_sites_loaded(last_day)
        print "Sites loaded: %s" % (datetime.datetime.now() - now)
        cls.collect_statistics_stories_shared(last_day)
        print "Stories shared: %s" % (datetime.datetime.now() - now)
        
    @classmethod
    def collect_statistics_feeds_fetched(cls, last_day=None):
        if not last_day:
            last_day = datetime.datetime.now() - datetime.timedelta(hours=24)
        last_month = datetime.datetime.now() - datetime.timedelta(days=30)
        
        feeds_fetched = MFeedFetchHistory.objects.filter(fetch_date__gte=last_day).count()
        cls.objects(key='feeds_fetched').update_one(upsert=True, set__key='feeds_fetched', set__value=feeds_fetched)
        pages_fetched = MPageFetchHistory.objects.filter(fetch_date__gte=last_day).count()
        cls.objects(key='pages_fetched').update_one(upsert=True, set__key='pages_fetched', set__value=pages_fetched)
        feeds_pushed = MFeedPushHistory.objects.filter(push_date__gte=last_day).count()
        cls.objects(key='feeds_pushed').update_one(upsert=True, set__key='feeds_pushed', set__value=feeds_pushed)
        
        from utils.feed_functions import timelimit, TimeoutError
        @timelimit(60)
        def delete_old_history():
            MFeedFetchHistory.objects(fetch_date__lt=last_day, status_code__in=[200, 304]).delete()
            MPageFetchHistory.objects(fetch_date__lt=last_day, status_code__in=[200, 304]).delete()
            MFeedFetchHistory.objects(fetch_date__lt=last_month).delete()
            MPageFetchHistory.objects(fetch_date__lt=last_month).delete()
            MFeedPushHistory.objects(push_date__lt=last_month).delete()
        try:
            delete_old_history()
        except TimeoutError:
            print "Timed out on deleting old history. Shit."
        
        return feeds_fetched
        
    @classmethod
    def collect_statistics_premium_users(cls, last_day=None):
        if not last_day:
            last_day = datetime.datetime.now() - datetime.timedelta(hours=24)
            
        premium_users = Profile.objects.filter(last_seen_on__gte=last_day, is_premium=True).count()
        cls.objects(key='premium_users').update_one(upsert=True, set__key='premium_users', set__value=premium_users)
        
        return premium_users
    
    @classmethod
    def collect_statistics_standard_users(cls, last_day=None):
        if not last_day:
            last_day = datetime.datetime.now() - datetime.timedelta(hours=24)
        
        standard_users = Profile.objects.filter(last_seen_on__gte=last_day, is_premium=False).count()
        cls.objects(key='standard_users').update_one(upsert=True, set__key='standard_users', set__value=standard_users)
        
        return standard_users
    
    @classmethod
    def collect_statistics_sites_loaded(cls, last_day=None):
        if not last_day:
            last_day = datetime.datetime.now() - datetime.timedelta(hours=24)
        now = datetime.datetime.now()
        sites_loaded = []
        avg_time_taken = []
        
        for hour in range(24):
            start_hours_ago = now - datetime.timedelta(hours=hour)
            end_hours_ago = now - datetime.timedelta(hours=hour+1)
            aggregates = dict(count=Count('loadtime'), avg=Avg('loadtime'))
            load_times = FeedLoadtime.objects.filter(
                date_accessed__lte=start_hours_ago, 
                date_accessed__gte=end_hours_ago
            ).aggregate(**aggregates)
            sites_loaded.append(load_times['count'] or 0)
            avg_time_taken.append(load_times['avg'] or 0)
        sites_loaded.reverse()
        avg_time_taken.reverse()
        
        values = (
            ('sites_loaded',            json.encode(sites_loaded)),
            ('avg_time_taken',          json.encode(avg_time_taken)),
            ('latest_sites_loaded',     sites_loaded[-1]),
            ('latest_avg_time_taken',   avg_time_taken[-1]),
            ('max_sites_loaded',        max(sites_loaded)),
            ('max_avg_time_taken',      max(1, max(avg_time_taken))),
        )
        for key, value in values:
            cls.objects(key=key).update_one(upsert=True, set__key=key, set__value=value)
            
    @classmethod
    def collect_statistics_stories_shared(cls, last_day=None):
        if not last_day:
            last_day = datetime.datetime.now() - datetime.timedelta(hours=24)
        now = datetime.datetime.now()
        stories_shared = []
        
        for hour in range(24):
            start_hours_ago = now - datetime.timedelta(hours=hour)
            end_hours_ago = now - datetime.timedelta(hours=hour+1)
            shares = MSharedStory.objects.filter(
                shared_date__lte=start_hours_ago, 
                shared_date__gte=end_hours_ago
            ).count()
            stories_shared.append(shares)

        stories_shared.reverse()
        
        values = (
            ('stories_shared',        json.encode(stories_shared)),
            ('latest_stories_shared', stories_shared[-1]),
            ('max_stories_shared',    max(stories_shared)),
        )
        for key, value in values:
            cls.objects(key=key).update_one(upsert=True, set__key=key, set__value=value)
    
    @classmethod
    def delete_old_stats(cls):
        now = datetime.datetime.now()
        old_age = now - datetime.timedelta(days=7)
        FeedLoadtime.objects.filter(date_accessed__lte=old_age).delete()

class MFeedback(mongo.Document):
    date    = mongo.StringField()
    summary = mongo.StringField()
    subject = mongo.StringField()
    url     = mongo.StringField()
    style   = mongo.StringField()
    order   = mongo.IntField()
    
    meta = {
        'collection': 'feedback',
        'allow_inheritance': False,
        'indexes': ['style'],
        'ordering': ['order'],
    }
    
    def __unicode__(self):
        return "%s: (%s) %s" % (self.style, self.date, self.subject)
        
    @classmethod
    def collect_feedback(cls):
        data = urllib2.urlopen('https://getsatisfaction.com/newsblur/topics.widget').read()
        data = json.decode(data[1:-1])
        i    = 0
        if len(data):
            cls.objects.delete()
            for feedback in data:
                feedback['order'] = i
                i += 1
                for removal in ['about', 'less than']:
                    if removal in feedback['date']:
                        feedback['date'] = feedback['date'].replace(removal, '')
            for feedback in data:
                # Convert unicode to strings.
                fb = dict([(str(k), v) for k, v in feedback.items()])
                cls.objects.create(**fb)
    
    @classmethod
    def all(cls):
        feedbacks = cls.objects.all()[:4]

        return feedbacks