from enum import Enum

class PaymentType(Enum):
    CREDIT_CARD = "CREDIT_CARD"
    BOLETO = "BOLETO"
    PIX = "PIX"
    DEBIT_CARD = "DEBIT_CARD"
    UNDEFINED = "UNDEFINED"