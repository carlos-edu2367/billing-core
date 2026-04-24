from app.application.repositories.customer_repo import Customer, CustomerRepository
from app.application.dtos.request.customer import CreateCustomerDTO
from app.application.interfaces.gateway_provider import GetGateway
from app.application.interfaces.uow_provider import UowProvider
from app.domain.errors import DomainError
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.system import System
from app.domain.value_objects.cnpj import CNPJ
from app.domain.value_objects.cpf import CPF
from app.domain.value_objects.email import Email

class CreateCustomer():
    def __init__(self, uow: UowProvider, get_gateway: GetGateway, repo: CustomerRepository):
        self.uow = uow
        self.get_gateway = get_gateway
        self.repo = repo

    async def execute(self, dtos: CreateCustomerDTO, system: System, gateway_provider: GatewayProvider) -> str: # Retorna cus_id
        gateway = self.get_gateway.get(gateway_provider)
        email = Email(dtos.email)
        cpf = None
        cnpj = None
        if dtos.cpf:
            cpf = CPF(dtos.cpf)
        elif dtos.cnpj:
            cnpj = CNPJ(dtos.cnpj)
        else:
            raise DomainError("É necessário cpf ou cnpj")
        
        cus = Customer(
            nome=dtos.nome_completo,
            email=email,
            cpf=cpf,
            cnpj=cnpj,
            system=system,
            system_customer_id=dtos.system_customer_id,
            gateway_provider=gateway_provider,
        )

        response = await gateway.create_customer(
            name=cus.nome,
            cpfCnpj=cus.return_cpf_cnpj(),
            email=cus.email.value,
            external_reference=cus.system_customer_id
        )

        cus.bind_provider_customer(response.cus_id)
        await self.repo.save(cus)
        await self.uow.commit()

        return response.cus_id
