import asyncio
import hashlib
import orjson
from typing import Any, Dict, List, Optional, Tuple, Union


class ExtractedResponse:
    """
    A class representing an extracted response.

    Attributes:
        event_type (str): The type of the response event.
        subscription_id (str): The subscription ID associated with the response.
        results (Any): The response data.
        comment (str): Additional comment for the response.
        rate_limit_response (Tuple[str, Optional[str], str, Optional[str]]): Rate limit response tuple.
        duplicate_response (Tuple[str, Optional[str], str, Optional[str]]): Duplicate response tuple.

    Methods:
        format_response(): Formats the response based on the event type.

    """

    def __init__(self, response_data, logger):
        """
        Initializes the ExtractedResponse object.

        Args:
            response_data (dict): The response data.

        """
        self.logger = logger
        self.event_type = response_data["event"]
        self.subscription_id = response_data["subscription_id"]
        self.message = response_data.get("message", "")
        try:
            self.results = response_data["results_json"]
        except orjson.JSONDecodeError as json_error:
            logger.error(
                f"Error decoding JSON message in Extracted Response: {json_error}."
            )
            self.results = ""

    async def format_response(self):
        """
        Formats the response based on the event type.

        Returns:
            Union[Tuple[str, Optional[str], str, Optional[str]], List[Tuple[str, Optional[str], Dict[str, Any]]], Tuple[str, Optional[str]]]: The formatted response.

        """
        if self.event_type == "OK":
            client_response: Tuple[str, Optional[str], str, Optional[str]] = (
                self.event_type,
                self.subscription_id,
                self.results,
                self.message,
            )

        else:
            # Return EOSE
            client_response: Tuple[str, Optional[str]] = (
                self.event_type,
                self.subscription_id,
            )

        return client_response

    async def send_event_loop(self, response_list, websocket, logger) -> None:
        """
        Sends a tuple of event items to a WebSocket using a faster JSON library (orjson).

        Parameters:
            response_list (List[Dict]): A list of dictionaries representing event items.
            websocket (websockets.WebSocketClientProtocol): The WebSocket connection to send the events to.
        """
        try:
            tasks = tuple(
                asyncio.create_task(
                    websocket.send(
                        orjson.dumps(
                            (self.event_type, self.subscription_id, event_item)
                        ).decode("utf-8")
                    )
                )
                for event_item in response_list
            )
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Error while sending events: {e}")


class WebsocketMessages:
    """
    A class representing WebSocket messages.

    Attributes:
        event_type (str): The type of the WebSocket event.
        subscription_id (str): The subscription ID associated with the event.
        event_payload (Union[List[Dict[str, Any]], Dict[str, Any]]): The payload of the event.
        origin (str): The origin or referer of the WebSocket request.
        obfuscate_ip (function): A lambda function to obfuscate the client IP address.
        obfuscated_client_ip (str): The obfuscated client IP address.
        uuid (str): The unique identifier of the WebSocket connection.

    Methods:
        __init__(self, message: List[Union[str, Dict[str, Any]]], websocket): Initializes the WebSocketMessages object.

    """

    def __init__(self, message: List[Union[str, Dict[str, Any]]], websocket, logger):
        """
        Initializes the WebSocketMessages object.

        Args:
            message (List[Union[str, Dict[str, Any]]]): The WebSocket message.
            websocket: The WebSocket connection.

        """
        self.event_type = message[0]
        if self.event_type in ("REQ", "CLOSE"):
            self.subscription_id: str = message[1]
            raw_payload = message[2:]
            logger.debug(f"Raw payload is {raw_payload} and len {len(raw_payload)}")
            self.event_payload = raw_payload
        else:
            self.event_payload: Dict[str, Any] = message[1]
        headers = websocket.request_headers
        self.origin: str = headers.get("origin", "") or headers.get("referer", "")
        self.obfuscate_ip = lambda ip: hashlib.sha256(ip.encode("utf-8")).hexdigest()
        self.obfuscated_client_ip = self.obfuscate_ip("X-Real-IP") or headers.get(
            "X-Forwarded-For"
        )
        logger.debug(f"Client obfuscated IP is {self.obfuscated_client_ip}")
        self.uuid: str = websocket.id


class SubscriptionMatcher:
    """
    Matches a raw Redis event against filters defined in a REQ query.

    Attributes:
        filters (List[Dict[str, Any]]): A list of filter dictionaries.
    """

    def __init__(self, subscription_id: str, req_query: List, logger):
        """
        Initializes the FilterMatcher with the REQ query.

        Args:
            subscription_id (str): The subscription ID.
            req_query (List): The REQ query, typically containing filters.
            logger: Logger instance for debugging.
        """
        self.subscription_id = subscription_id
        self.filters = req_query
        self.logger = logger

    def match_event(self, event: Dict[str, Any]) -> bool:
        """
        Determines if a given event matches the filters.

        Args:
            event (Dict[str, Any]): The raw Redis event to match.

        Returns:
            bool: True if the event matches any of the filters, False otherwise.
        """
        for list_item in self.filters:
            for filter_, value in list_item.items():
                if filter == 'limit':
                    self.logger.debug('Ignoring limit filter')
                    continue
                self.logger.debug(f"Checking filter: {filter_}, value : {value}")
                combined = {filter_: value}
                if self._match_single_filter(combined, event):
                    self.logger.debug(f"Event matches filter: {filter_}")
                    return True
                else:
                    self.logger.debug("filter did not match the event.")

            self.logger.debug("Returning false")
            return False

    def _match_single_filter(
        self, filter_: Dict[str, Any], event: Dict[str, Any]
    ) -> bool:
        """
        Matches an event against a single filter.

        Args:
            filter_ (Dict[str, Any]): The filter to apply.
            event (Dict[str, Any]): The raw Redis event to match.

        Returns:
            bool: True if the event matches the filter, False otherwise.
        """
        for key, value in filter_.items():
            self.logger.debug(f"Checking key: {key}, value: {value}")

            if key == "kinds":
                if event.get("kind") not in value:
                    self.logger.debug(
                        f"Filter mismatch for 'kinds': {event.get('kind')} not in {value}"
                    )
                    return False
            elif key == "authors":
                if event.get("pubkey") not in value:
                    self.logger.debug(
                        f"Filter mismatch for 'authors': {event.get('pubkey')} not in {value}"
                    )
                    return False
            elif key.startswith("#"):
                tag_key = key[1:]
                if not any(
                    tag_key == tag[0] and any(v in tag[1] for v in value)
                    for tag in event.get("tags", [])
                ):
                    self.logger.debug(
                        f"Filter mismatch for tag '{key}': {event.get('tags', [])}"
                    )
                    return False
            elif key == "since":
                if event.get("created_at", 0) < value:
                    return False
            elif key == "until":
                if event.get("created_at", 0) > value:
                    return False
            elif key == "search":
                self.logger.debug(
                    f"Performing search for value '{value}' in content and tags"
                )
                content = event.get("content", "")
                tags = event.get("tags", [])
                self.logger.debug(f"Event content: {content}")
                self.logger.debug(f"Event tags: {tags}")

                # Check if `value` exists in content or tags
                if value.lower() not in content.lower() and not any(
                    value.lower() in tag_value.lower() for _, tag_value in tags
                ):
                    self.logger.debug(
                        f"Search mismatch: '{value}' not found in content or tags"
                    )
                    return False
            elif key == "id":
                if event.get("id", "") != value:
                    return False
            else:
                if key in event and event[key] != value:
                    self.logger.debug(
                        f"Filter mismatch for key '{key}': {event.get(key)} != {value}"
                    )
                    return False

        self.logger.debug("Filter matched successfully.")
        return True
