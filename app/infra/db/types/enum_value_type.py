from enum import Enum
from sqlalchemy.types import TypeDecorator, String


class EnumValueType(TypeDecorator):
    impl = String(50)
    cache_ok = True

    def __init__(self, enum_class: type[Enum], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enum_class = enum_class

    def process_bind_param(self, value, dialect):
        if value is None:
            return None

        if isinstance(value, self.enum_class):
            return value.value

        if isinstance(value, str):
            return self.enum_class(value).value  # valida a string

        raise ValueError(
            f"Invalid value for {self.enum_class.__name__}: {value}"
        )

    def process_result_value(self, value, dialect):
        if value is None:
            return None

        return self.enum_class(value)