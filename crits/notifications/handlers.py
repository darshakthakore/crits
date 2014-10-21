import datetime
import threading

from mongoengine.queryset import Q

from crits.core.class_mapper import class_from_id, details_url_from_obj
from crits.core.form_consts import NotificationType
from crits.notifications.notification import Notification


def get_notification_details(request, newer_than):
    """
    Generate the data to render the notification dialogs.

    :param request: The Django request.
    :type request: :class:`django.http.HttpRequest`
    :param newer_than: A filter that specifies that only notifications
                       newer than this time should be returned.
    :type newer_than: str in ISODate format.
    :returns: arguments (dict)
    """

    username = request.user.username
    notifications_list = []
    notifications = None
    latest_notification_time = None
    lock = NotificationLockManager.get_notification_lock(username)
    timeout = 0

    # Critical section, check if there are notifications to be consumed.
    lock.acquire()
    try:
        notifications = get_user_notifications(username, newer_than=newer_than)

        if len(notifications) > 0:
            latest_notification_time = str(notifications[0].created)
        else:
            # no new notifications -- block until time expiration or lock release
            lock.wait(60)

            # lock was released, check if there is any new information yet
            notifications = get_user_notifications(username, newer_than=newer_than)

            if len(notifications) > 0:
                latest_notification_time = str(notifications[0].created)
    finally:
        lock.release()

    if latest_notification_time is not None:
        acknowledgement_type = request.user.get_preference('toast_notifications', 'acknowledgement_type', 'sticky')

        if acknowledgement_type == 'timeout':
            timeout = request.user.get_preference('toast_notifications', 'timeout', 30) * 1000

    for notification in notifications:
        obj = class_from_id(notification.obj_type, notification.obj_id)
        details_url = details_url_from_obj(obj)
        header = generate_notification_header(obj)
        notification_type = notification.notification_type

        if notification_type is None or notification_type not in NotificationType.ALL:
            notification_type = NotificationType.ALERT

        notification_data = {
            "header": header,
            "message": notification.notification,
            "date_modified": str(notification.created),
            "link": details_url,
            "modified_by": notification.analyst,
            "id": str(notification.id),
            "type": notification_type,
        }

        notifications_list.append(notification_data)

    return {
        'notifications': notifications_list,
        'newest_notification': latest_notification_time,
        'server_time': str(datetime.datetime.now()),
        'timeout': timeout,
    }

def get_notifications_for_id(username, obj_id, obj_type):
    """
    Get notifications for a specific top-level object and user.

    :param username: The user to search for.
    :param obj_id: The ObjectId to search for.
    :type obj_id: str
    :param obj_type: The top-level object type.
    :type obj_type: str
    :returns: :class:`crits.core.crits_mongoengine.CritsQuerySet`
    """

    return Notification.objects(users=username,
                                obj_id=obj_id,
                                obj_type=obj_type)

def remove_notification(obj_id):
    """
    Remove an existing notification.

    :param obj_id: The top-level ObjectId to find the notification to remove.
    :type obj_id: str
    :returns: dict with keys "success" (boolean) and "message" (str).
    """

    notification = Notification.objects(id=obj_id).first()
    if not notification:
        message = "Could not find notification to remove!"
        result = {'success': False, 'message': message}
    else:
        notification.delete()
        message = "Notification removed successfully!"
        result = {'success': True, 'message': message}
    return result

def get_new_notifications():
    """
    Get any new notifications.
    """

    return Notification.objects(status="new")

def remove_user_from_notification(username, obj_id, obj_type):
    """
    Remove a user from the list of users for a notification.

    :param username: The user to remove.
    :type username: str
    :param obj_id: The ObjectId of the top-level object for this notification.
    :type obj_id: str
    :param obj_type: The top-level object type.
    :type obj_type: str
    :returns: dict with keys "success" (boolean) and "message" (str) if failed.
    """

    Notification.objects(obj_id=obj_id,
                         obj_type=obj_type).update(pull__users=username)
    return {'success': True}

def remove_user_from_notification_id(username, id):
    """
    Remove a user from the list of users for a notification.

    :param username: The user to remove.
    :type username: str
    :param obj_id: The ObjectId of the top-level object for this notification.
    :type obj_id: str
    :param obj_type: The top-level object type.
    :type obj_type: str
    :returns: dict with keys "success" (boolean) and "message" (str) if failed.
    """

    Notification.objects(id=id).update(pull__users=username)
    return {'success': True}

def remove_user_notifications(username):
    """
    Remove a user from all notifications.

    :param username: The user to remove.
    :type username: str
    :returns: dict with keys "success" (boolean) and "message" (str) if failed.
    """

    Notification.objects(users=username).update(pull__users=username)


def get_user_notifications(username, count=False, newer_than=None):
    """
    Get the notifications for a user.

    :param username: The user to get notifications for.
    :type username: str
    :param count: Only return the count.
    :type count:bool
    :returns: int, :class:`crits.core.crits_mongoengine.CritsQuerySet`
    """
    n = None

    if newer_than is None or newer_than == None:
        n = Notification.objects(users=username).order_by('-created')
    else:
        n = Notification.objects(Q(users=username) & Q(created__gt=newer_than)).order_by('-created')

    if count:
        return len(n)
    else:
        return n

class NotificationLockManager(object):
    """
    Manager class to handle locks for notifications.
    """

    __notification_mutex__ = threading.Lock()
    __notification_locks__ = {}

    @classmethod
    def get_notification_lock(cls, username):
        """
        @threadsafe

        Gets a notification lock for the specified user, if it doesn't exist
        then one is created.
        """

        if username not in cls.__notification_locks__:
            # notification lock doesn't exist for user, create new lock
            cls.__notification_mutex__.acquire()
            try:
                # safe double checked locking
                if username not in cls.__notification_locks__:
                    cls.__notification_locks__[username] = threading.Condition()
            finally:
                cls.__notification_mutex__.release()

        return cls.__notification_locks__.get(username)

"""
The following generate_*_header() functions generate a meaningful description
for that specific object type.
"""
def generate_actor_header(obj):
    return "Actor: %s" % (obj.name)

def generate_backdoor_header(obj):
    return "Backdoor: %s" % (obj.name)

def generate_campaign_header(obj):
    return "Campaign: %s" % (obj.name)

def generate_certificate_header(obj):
    return "Certificate: %s" % (obj.filename)

def generate_domain_header(obj):
    return "Domain: %s" % (obj.domain)

def generate_email_header(obj):
    return "Email: %s" % (obj.subject)

def generate_event_header(obj):
    return "Event: %s" % (obj.title)

def generate_indicator_header(obj):
    return "Indicator: %s - %s" % (obj.ind_type, obj.value)

def generate_ip_header(obj):
    return "IP: %s" % (obj.ip)

def generate_pcap_header(obj):
    return "PCAP: %s" % (obj.filename)

def generate_raw_data_header(obj):
    return "RawData: %s (version %s)" % (obj.title, obj.version)

def generate_sample_header(obj):
    return "Sample: %s" % (obj.filename)

def generate_screenshot_header(obj):
    return "Screenshot: %s" % (obj.filename)

def generate_target_header(obj):
    return "Target: %s" % (obj.email_address)

notification_header_handler = {
    "Actor": generate_actor_header,
    "Campaign": generate_campaign_header,
    "Certificate": generate_certificate_header,
    "Domain": generate_domain_header,
    "Email": generate_email_header,
    "Event": generate_event_header,
    "Indicator": generate_indicator_header,
    "IP": generate_ip_header,
    "PCAP": generate_pcap_header,
    "RawData": generate_raw_data_header,
    "Sample": generate_sample_header,
    "Screenshot": generate_screenshot_header,
    "Target": generate_target_header,
}

def generate_notification_header(obj):
    """
    Generates notification header information based upon the object -- this is
    used to preface the notification's context.

    Could possibly be used for "Favorites" descriptions as well.

    :param obj: The top-level object instantiated class.
    :type obj: class which inherits from
               :class:`crits.core.crits_mongoengine.CritsBaseAttributes`.
    :returns: str with a human readable identification of the object
    """

    generate_notification_header_handler = notification_header_handler.get(obj._meta['crits_type'])

    if generate_notification_header_handler is not None:
        return generate_notification_header_handler(obj)
    else:
        return "%s: %s" % (type, str(obj.id))
