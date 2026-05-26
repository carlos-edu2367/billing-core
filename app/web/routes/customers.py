from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dtos.request.customer import CreateCustomerDTO
from app.application.use_cases.create_customer import CreateCustomer
from app.domain.enums.gateway_provider import GatewayProvider
from app.infra.db.setup import get_db
from app.infra.interfaces.gateway_provider import GetGatewayInfra
from app.infra.interfaces.uow_provider import UowProvider
from app.infra.repo.customer_repo import CustomerRepositoryINFRA
from app.web.dependencies.rate_limit import internal_rate_limit
from app.web.dependencies.security import AuthContext, require_internal_auth
from app.web.schemas.common import build_error_responses
from app.web.schemas.customer import CreateCustomerRequest, CreateCustomerResponse


router = APIRouter(prefix="/v1/customers", tags=["customers"])


async def get_create_customer_use_case(
    db: AsyncSession = Depends(get_db),
) -> CreateCustomer:
    uow = UowProvider(db)
    repo = CustomerRepositoryINFRA(db)
    gateway = GetGatewayInfra()
    return CreateCustomer(uow=uow, get_gateway=gateway, repo=repo)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateCustomerResponse,
    summary="Criar cliente",
    description=(
        "Registra um novo cliente no provedor de pagamento (Asaas). "
        "Idempotente por CPF/CNPJ: se o cliente já existir no Asaas, retorna o mesmo provider_customer_id."
    ),
    responses=build_error_responses(400, 401, 403, 422, 429, 500),
)
async def create_customer(
    payload: CreateCustomerRequest,
    auth: AuthContext = Depends(require_internal_auth("customers:create")),
    _rate_limiter=Depends(internal_rate_limit()),
    use_case: CreateCustomer = Depends(get_create_customer_use_case),
):
    if auth.system != payload.system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sistema sem permissao para criar esse cliente.",
        )

    dto = CreateCustomerDTO(
        cpf=payload.cpf,
        cnpj=payload.cnpj,
        nome_completo=payload.nome_completo,
        email=payload.email,
        system_customer_id=payload.system_customer_id,
    )

    provider_customer_id = await use_case.execute(dto, auth.system, GatewayProvider.ASAAS)
    return CreateCustomerResponse(provider_customer_id=provider_customer_id)
