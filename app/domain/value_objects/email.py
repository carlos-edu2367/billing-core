from __future__ import annotations
from app.domain.errors import InvalidEmailError

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Email:
    value: str

    EMAIL_REGEX = re.compile(
        r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+"
        r"@"
        r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,}$"
    )

    def __post_init__(self) -> None:
        normalized = self._normalize(self.value)

        if not self._is_valid(normalized):
            raise InvalidEmailError(f"Invalid email address: {self.value}")

        object.__setattr__(self, "value", normalized)

    @staticmethod
    def _normalize(email: str) -> str:
        return email.strip().lower()

    @classmethod
    def _is_valid(cls, email: str) -> bool:
        if not email:
            return False

        if len(email) > 254:
            return False

        return bool(cls.EMAIL_REGEX.fullmatch(email))

    @property
    def local_part(self) -> str:
        return self.value.split("@", 1)[0]

    @property
    def domain(self) -> str:
        return self.value.split("@", 1)[1]

    def __str__(self) -> str:
        return self.value