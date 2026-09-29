from crossfoot.doctypes.bank_statement.layouts.hdfc_savings_v1 import HdfcSavingsV1
from crossfoot.doctypes.bank_statement.layouts.sbi_savings_v1 import SbiSavingsV1
from crossfoot.doctypes.bank_statement.registry import register

register(HdfcSavingsV1())
register(SbiSavingsV1())
