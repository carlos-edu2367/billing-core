from __future__ import annotations

from dataclasses import dataclass
from app.domain.errors import InvalidCPFError


@dataclass(frozen=True, slots=True)
class CPF:
    value: str  # stored normalized: only digits

    def __post_init__(self) -> None:
        normalized = self._normalize(self.value)

        if not self._is_valid(normalized):
            raise InvalidCPFError(f"Invalid CPF: {self.value}")

        object.__setattr__(self, "value", normalized)

    @classmethod
    def create(cls, value: str) -> "CPF":
        return cls(value)

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(filter(str.isdigit, value or ""))

    @classmethod
    def _is_valid(cls, cpf: str) -> bool:
        if len(cpf) != 11:
            return False

        # bloqueia sequências repetidas tipo 11111111111
        if cpf == cpf[0] * 11:
            return False

        # 1º dígito verificador
        sum_1 = sum(int(cpf[i]) * (10 - i) for i in range(9))
        digit_1 = (sum_1 * 10) % 11
        digit_1 = 0 if digit_1 == 10 else digit_1

        if digit_1 != int(cpf[9]):
            return False

        # 2º dígito verificador
        sum_2 = sum(int(cpf[i]) * (11 - i) for i in range(10))
        digit_2 = (sum_2 * 10) % 11
        digit_2 = 0 if digit_2 == 10 else digit_2

        return digit_2 == int(cpf[10])

    @property
    def formatted(self) -> str:
        """
        Returns CPF in the format XXX.XXX.XXX-XX
        """
        return f"{self.value[:3]}.{self.value[3:6]}.{self.value[6:9]}-{self.value[9:]}"

    def __str__(self) -> str:
        return self.value