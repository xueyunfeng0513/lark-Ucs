"""Official Feishu SDK persistent connection, sharing the webhook job pipeline."""
import json
import logging

import lark_oapi as lark

from .ingress import accept_message


def enqueue_sdk_event(data, settings, store):
    try:
        accept_message(json.loads(lark.JSON.marshal(data)), settings, store)
    except (ValueError, KeyError, TypeError, AttributeError):
        logging.getLogger(__name__).warning("Ignored malformed SDK message event")
    # Database failures propagate to the SDK, which responds with failure rather
    # than acknowledging an event that has not been durably accepted.


def build_client(settings, store):
    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(lambda data: enqueue_sdk_event(data, settings, store))
               .build())
    return lark.ws.Client(settings.app_id, settings.app_secret, event_handler=handler,
                          log_level=lark.LogLevel.INFO)
