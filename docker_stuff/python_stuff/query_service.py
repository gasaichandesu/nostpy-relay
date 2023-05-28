import os
import json
import logging
import redis
from ddtrace import tracer
import aiohttp
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker, class_mapper
from sqlalchemy import create_engine, Column, String, Integer, JSON
from sqlalchemy.ext.declarative import declarative_base

tracer.configure(hostname='172.28.0.5', port=8126)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logging.basicConfig(level=logging.DEBUG)

DATABASE_URL = os.environ.get("DATABASE_URL")
logger.debug(f"DATABASE_URL value: {DATABASE_URL}")

redis_client = redis.Redis(host='172.28.0.6', port=6379)

engine = create_engine(DATABASE_URL, echo=True)
Base = declarative_base()


class Event(Base):
    __tablename__ = 'event'

    id = Column(String, primary_key=True, index=True)
    pubkey = Column(String, index=True)
    kind = Column(Integer, index=True)
    created_at = Column(Integer, index=True)
    tags = Column(JSON)
    content = Column(String)
    sig = Column(String)

    def __init__(self, id: str, pubkey: str, kind: int, created_at: int, tags: list, content: str, sig: str):
        self.id = id
        self.pubkey = pubkey
        self.kind = kind
        self.created_at = created_at
        self.tags = tags
        self.content = content
        self.sig = sig


app = FastAPI()


def serialize(model):
    # Helper function to convert an SQLAlchemy model instance to a dictionary
    columns = [c.key for c in class_mapper(model.__class__).columns]
    return dict((c, getattr(model, c)) for c in columns)

@app.post("/subscription")
async def handle_subscription(request: Request):
    try:
        response = None
        payload = await request.json()
        subscription_dict = payload.get('event_dict')
        subscription_id = payload.get('subscription_id')
        origin = payload.get('origin')
        filters = subscription_dict

        ## Redis cache key from subscription filters
        #cache_key = json.dumps(filters)
#
        ## Check if the result is already cached
        #cached_result = redis_client.get(cache_key)
        #if cached_result:
        #    logger.debug("Result found in Redis cache")
        #    result = json.loads(cached_result)
        #    logger.debug(f"Result: {result}")
        #    logger.debug(f"Len of redis response is: {len(result)}")
        #    if len(result) != 0:
        #        response = {'event': "EVENT", 'subscription_id': subscription_id, 'results_json': result}
        #        logger.debug(f"Redis JSON was went to WS handler")
        #    else:
        #        logger.debug("Result not found in Redis cache")

        if not response:
            Session = sessionmaker(bind=engine)
            session = Session()
            try:
                query = session.query(Event)
                if filters.get("ids"):
                    query = query.filter(Event.id.in_(filters.get("ids")))
                if filters.get("authors"):
                    query = query.filter(Event.pubkey.in_(filters.get("authors")))
                if filters.get("kinds"):
                    query = query.filter(Event.kind.in_(filters.get("kinds")))
                if filters.get("#e"):
                    query = query.filter(Event.tags.any(lambda tag: tag[0] == 'e' and tag[1] in filters.get("#e")))
                if filters.get("#p"):
                    query = query.filter(Event.tags.any(lambda tag: tag[0] == 'p' and tag[1] in filters.get("#p")))
                if filters.get("#d"):
                    query = query.filter(Event.tags.any(lambda tag: tag[0] == 'd' and tag[1] in filters.get("#d")))
                if filters.get("since"):
                    query = query.filter(Event.created_at > filters.get("since"))
                if filters.get("until"):
                    query = query.filter(Event.created_at < filters.get("until"))
                query_result = query.limit(filters.get("limit", 100)).all()

                redis_filters = []
                for event in query_result:
                    serialized_event = serialize(event)
                    redis_filters.append(serialized_event)
                #redis_client.set(cache_key, json.dumps(redis_filters), ex=3600)

                logger.debug("Result saved in Redis cache")
                logger.debug(f"Data type of redis_filters: {type(redis_filters)}, Length of redis_filters variable is {len(redis_filters)}")
                
                if len(redis_filters) == 0:
                    response = None #{'event': "EOSE", 'subscription_id': subscription_id, 'results_json': "None"}
                    logger.debug(f"Data type of response: {type(response)}, End of stream event response: {response}")
                else:
                    response = {'event': "EVENT", 'subscription_id': subscription_id, 'results_json': redis_filters}
                    logger.debug(f"Data type of response: {type(response)}, Sending postgres query results: {response}")
            except Exception as e:
                error_message = str(e)
                logger.error(f"Error occurred: {error_message}")
                raise HTTPException(status_code=500, detail="An error occurred while processing the subscription")
            finally:
                session.close()

    except Exception as e:
        error_message = str(e)
        logger.error(f"Error occurred: {error_message}")
        raise HTTPException(status_code=500, detail="An error occurred while processing the subscription")
    finally:
        if response is None:
            logger.debug(f"Response type is None = {response}")
            response = {'event': "EOSE", 'subscription_id': subscription_id, 'results_json': "None"}
        logger.debug(f"Finally block, returning JSON response to wh client {response}")
        return JSONResponse(content=response, status_code=200)


    
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=80)


