import pytest
from uuid import uuid4
from decimal import Decimal
from sqlalchemy.exc import IntegrityError

from app.application.dtos.request.customer import CreateCustomerDTO
from app.application.interfaces.gateway_provider import GetGateway, GetCustomerResponse
from app.application.use_cases.create_customer import CreateCustomer
from app.domain.entities.customer import Customer
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.system import System
from app.domain.value_objects.cpf import CPF
from app.domain.value_objects.email import Email
from app.domain.errors import NotFoundError


class FakeGateway:
    def __init__(self, provider_id="cus-123"):
        self.provider_id = provider_id
        self.calls = 0

    async def create_customer(self, name, cpfCnpj, email, external_reference):
        self.calls += 1
        return GetCustomerResponse(
            cus_id=self.provider_id,
            name=name,
            email=email,
            external_reference=external_reference,
            deleted=False
        )


class FakeGetGateway:
    def __init__(self, gateway):
        self._gateway = gateway

    def get(self, gateway_provider):
        return self._gateway


class FakeCustomerRepo:
    def __init__(self, existing=None, fail_on_save=False):
        self.existing = existing
        self.saved = []
        self.fail_on_save = fail_on_save
        self.get_calls = 0

    async def get_by_system_id_and_system(self, system_id, system):
        self.get_calls += 1
        if self.fail_on_save and self.get_calls == 1:
            raise NotFoundError("Customer not found")
        if self.existing and self.existing.system_customer_id == system_id and self.existing.system == system:
            return self.existing
        raise NotFoundError("Customer not found")

    async def save(self, customer: Customer):
        if self.fail_on_save:
            # Simulate a unique constraint violation when saving in database
            raise IntegrityError("mock statement", "mock params", "mock orig")
        if customer.id is None:
            customer.id = uuid4()
        self.saved.append(customer)
        self.existing = customer
        return customer


class FakeUow:
    def __init__(self):
        self.commit_called = 0
        self.rollback_called = 0

    async def commit(self):
        self.commit_called += 1

    async def rollback(self):
        self.rollback_called += 1


@pytest.mark.asyncio
async def test_create_customer_sequential_idempotency():
    gateway = FakeGateway()
    repo = FakeCustomerRepo()
    uow = FakeUow()
    use_case = CreateCustomer(uow=uow, get_gateway=FakeGetGateway(gateway), repo=repo)

    dto = CreateCustomerDTO(
        nome_completo="Carlos Silva",
        email="carlos@example.com",
        cpf="39053344705",
        cnpj=None,
        system_customer_id="cust-42",
    )

    # First call creates customer
    id_1 = await use_case.execute(dto, System.MARKETFY, GatewayProvider.ASAAS)
    assert id_1 == "cus-123"
    assert gateway.calls == 1
    assert len(repo.saved) == 1

    # Second call returns existing customer ID without calling gateway again
    id_2 = await use_case.execute(dto, System.MARKETFY, GatewayProvider.ASAAS)
    assert id_2 == "cus-123"
    assert gateway.calls == 1  # No additional gateway call
    assert len(repo.saved) == 1  # No additional save


@pytest.mark.asyncio
async def test_create_customer_concurrent_conflict_handling():
    gateway = FakeGateway()
    existing_cus = Customer(
        nome="Carlos Silva",
        email=Email("carlos@example.com"),
        cpf=CPF("39053344705"),
        provider_customer_id="cus-123",
        system_customer_id="cust-42",
        gateway_provider=GatewayProvider.ASAAS,
        system=System.MARKETFY
    )
    # Repo fails on save to simulate concurrent conflict, but has the existing record available when queried
    repo = FakeCustomerRepo(existing=existing_cus, fail_on_save=True)
    uow = FakeUow()
    use_case = CreateCustomer(uow=uow, get_gateway=FakeGetGateway(gateway), repo=repo)

    dto = CreateCustomerDTO(
        nome_completo="Carlos Silva",
        email="carlos@example.com",
        cpf="39053344705",
        cnpj=None,
        system_customer_id="cust-42",
    )

    # Usecase handles IntegrityError, rolls back, and returns the existing provider customer ID
    cus_id = await use_case.execute(dto, System.MARKETFY, GatewayProvider.ASAAS)

    assert cus_id == "cus-123"
    assert uow.rollback_called == 1
    assert gateway.calls == 1
