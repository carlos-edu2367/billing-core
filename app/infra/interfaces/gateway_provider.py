from app.application.interfaces.gateway_provider import GetGateway, InterfaceGateway
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.errors import UnsupportedGatewayError
from app.infra.interfaces.asaas_provider import AsaasProvider


class GetGatewayInfra(GetGateway):
    def __init__(self):
        self.providers: dict[GatewayProvider, type[InterfaceGateway]] = {
            GatewayProvider.ASAAS: AsaasProvider,
        }

    def get(self, gateway: GatewayProvider) -> InterfaceGateway:
        provider_class = self.providers.get(gateway)
        if provider_class is None:
            raise UnsupportedGatewayError(f"Gateway não suportado: {gateway.value}")

        return provider_class()
