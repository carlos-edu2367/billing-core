from uuid import UUID

from app.domain.errors import DomainError
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.system import System
from app.domain.value_objects.cnpj import CNPJ
from app.domain.value_objects.cpf import CPF
from app.domain.value_objects.email import Email


class Customer():
    def __init__(
        self,
        nome: str,
        email: Email,
        cpf: CPF | None = None,
        cnpj: CNPJ | None = None,
        id: UUID = None,
        provider_customer_id: str | None = None,
        system_customer_id: str | None = None,
        gateway_provider: GatewayProvider = GatewayProvider.ASAAS,
        system: System = System.NEECTIFY_SHOP,
    ):
        normalized_name = (nome or "").strip()
        normalized_system_customer_id = (system_customer_id or "").strip()

        if not normalized_name:
            raise DomainError("Customer precisa ter nome.")

        if not normalized_system_customer_id:
            raise DomainError("Customer precisa ter identificador do sistema.")

        if cpf is None and cnpj is None:
            raise DomainError("Customer precisa ter CPF ou CNPJ.")

        if cpf is not None and cnpj is not None:
            raise DomainError("Customer deve ter apenas um documento principal.")

        self.cpf = cpf
        self.cnpj = cnpj
        self.nome = normalized_name
        self.email = email
        self.id = id
        self.provider_customer_id = provider_customer_id
        self.system_customer_id = normalized_system_customer_id
        self.gateway_provider = gateway_provider
        self.system = system

    def bind_provider_customer(self, provider_customer_id: str):
        normalized_provider_customer_id = (provider_customer_id or "").strip()
        if not normalized_provider_customer_id:
            raise DomainError("Customer precisa ter identificador do gateway.")

        self.provider_customer_id = normalized_provider_customer_id

    def has_provider_binding(self) -> bool:
        return bool(self.provider_customer_id)

    def return_cpf_cnpj(self) -> str:
        if self.cpf is not None:
            return self.cpf.value

        if self.cnpj is not None:
            return self.cnpj.value

        raise DomainError("Customer sem documento principal.")
