from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def get_engine(url: str):
    return create_engine(url)


def get_session(url: str) -> Session:
    return Session(get_engine(url))
