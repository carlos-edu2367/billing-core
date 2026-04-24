from dataclasses import dataclass

@dataclass
class CreateCustomerDTO:
    cpf: str | None
    cnpj: str | None
    nome_completo: str
    email: str
    system_customer_id: str
