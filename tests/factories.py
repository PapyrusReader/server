from polyfactory.factories.pydantic_factory import ModelFactory

from papyrus.schemas.book import BookCreate
from papyrus.schemas.shelf import CreateShelfRequest


class BookCreateFactory(ModelFactory[BookCreate]):
    __model__ = BookCreate


class CreateShelfRequestFactory(ModelFactory[CreateShelfRequest]):
    __model__ = CreateShelfRequest
