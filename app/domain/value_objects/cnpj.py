from __future__ import annotations

from dataclasses import dataclass
from app.domain.errors import InvalidCNPJError


@dataclass(frozen=True, slots=True)
class CNPJ:
    value: str  # stored normalized: only digits

    def __post_init__(self) -> None:
        normalized = self._normalize(self.value)

        if not self._is_valid(normalized):
            raise InvalidCNPJError(f"Invalid CNPJ: {self.value}")

        object.__setattr__(self, "value", normalized)

    @classmethod
    def create(cls, value: str) -> "CNPJ":
        return cls(value)

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(filter(str.isdigit, value or ""))

    @classmethod
    def _is_valid(cls, cnpj: str) -> bool:
        if len(cnpj) != 14:
            return False

        # bloqueia sequências repetidas tipo 11111111111111
        if cnpj == cnpj[0] * 14:
            return False

        def calculate_digit(base: str, weights: list[int]) -> int:
            total = sum(int(digit) * weight for digit, weight in zip(base, weights))
            remainder = total % 11
            return 0 if remainder < 2 else 11 - remainder

        weights_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        weights_2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]

        digit_1 = calculate_digit(cnpj[:12], weights_1)
        if digit_1 != int(cnpj[12]):
            return False

        digit_2 = calculate_digit(cnpj[:13], weights_2)
        return digit_2 == int(cnpj[13])

    @property
    def formatted(self) -> str:
        """
        Returns CNPJ in the format XX.XXX.XXX/XXXX-XX
        """
        return (
            f"{self.value[:2]}.{self.value[2:5]}.{self.value[5:8]}/"
            f"{self.value[8:12]}-{self.value[12:]}"
        )

    def __str__(self) -> str:
        return self.value