from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums.system import System


class CreateCustomerRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "nome_completo": "João Silva",
                "email": "joao@exemplo.com",
                "cpf": "390.533.447-05",
                "system_customer_id": "user_42",
                "system": "neectify_food",
            }
        }
    )

    nome_completo: str = Field(..., min_length=2, max_length=255, description="Nome completo do cliente.")
    email: str = Field(..., min_length=5, max_length=255, description="E-mail do cliente.")
    cpf: str | None = Field(default=None, description="CPF do cliente (pessoa física). Exatamente um de cpf/cnpj.")
    cnpj: str | None = Field(default=None, description="CNPJ do cliente (pessoa jurídica). Exatamente um de cpf/cnpj.")
    system_customer_id: str = Field(..., min_length=1, max_length=128, description="ID do cliente no seu sistema.")
    system: System = Field(..., description="Sistema Neectify de origem.")

    @model_validator(mode="after")
    def validate_document(self) -> "CreateCustomerRequest":
        has_cpf = bool(self.cpf and self.cpf.strip())
        has_cnpj = bool(self.cnpj and self.cnpj.strip())
        if has_cpf and has_cnpj:
            raise ValueError("Informe apenas cpf ou cnpj, não ambos.")
        if not has_cpf and not has_cnpj:
            raise ValueError("É necessário informar cpf ou cnpj.")
        return self


class CreateCustomerResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider_customer_id": "cus_000005113076",
            }
        }
    )

    provider_customer_id: str = Field(..., description="ID do cliente no provedor de pagamento (Asaas).")
