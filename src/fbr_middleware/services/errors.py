"""PRAL sales error-code catalogue (spec v1.12 section 7): code -> BRIEF_MSG_DESC.

Used to fill ``errorDescriptions`` in the envelope so SAP users see the explanation next to the
raw ``errorCode``. Purchase codes (section 8, 0156-0177) are not included: no purchase endpoint
exists in the DI API.
"""

from __future__ import annotations

from ..models.api import ErrorDescription

ERROR_CATALOGUE: dict[str, str] = {
    "0001": "Seller is not registered for sales tax, please provide valid seller registration/NTN.",
    "0002": "Buyer Registration Number or NTN is not in proper format, please provide buyer registration number in 13 digits or NTN in 7 or 9 digits",
    "0003": "Invoice type is not valid or empty, please provide valid invoice type",
    "0005": 'Invoice date is not in proper format, please provide invoice date in "YYYY-MM-DD" format. For example: 2025-05-25',
    "0006": "Sales invoice does not exist against STWH",
    "0007": "Selected invoice type is not associated with proper registration number, please select actual invoice type",
    "0008": "ST withheld at source is not equal to zero or sales tax, please enter ST withheld at source zero or equal to sales tax",
    "0009": "Buyer Registration Number cannot be empty, please provide proper buyer registration number",
    "0010": "Buyer Name cannot be empty, please provide valid buyer name",
    "0011": "Invoice type cannot be empty, please provide valid invoice type",
    "0012": "Buyer Registration type cannot be empty, please provide valid Buyer Registration type",
    "0013": "Sale type cannot be empty/null, please provide valid sale type",
    "0018": "Sales Tax/FED cannot be empty, please valid provide Sales Tax/FED",
    "0019": "HS Code cannot be empty, please provide valid HS Code",
    "0020": "Rate field cannot be empty, please provide Rate",
    "0021": "Value of Sales Excl. ST /Quantity cannot be empty, Please provide valid Value of Sales Excl. ST /Quantity",
    "0022": "ST withheld at Source or STS Withheld cannot be empty, Please provide valid ST withheld at Source or STS Withheld",
    "0023": "Sales Tax cannot be empty, Please provide valid Sales Tax",
    "0024": "Sales Tax withheld cannot be empty, Please provide valid Sales Tax withheld",
    "0026": "Invoice Reference No. is mandatory requirement for debit/credit note. Please provide valid Invoice Reference No.",
    "0027": "Reason is mandatory requirement for debit/credit note. Please provide valid reason for debit/credit note",
    "0028": 'Reason is selected as "Others". Please provide valid remarks against this reason',
    "0029": "Debit/Credit note date should be equal or greater from original invoice date",
    "0030": "Unregistered distributer type not allowed before system cut of date",
    "0031": "Sales Tax is not mentioned, please provide Sales Tax",
    "0032": "User is not FTN holder, STWH can only be created for GOV/FTN Holders without sales invoice.",
    "0034": "Debit/Credit note can only be added within 180 days of original invoice date",
    "0035": "Note Date must be greater or equal to original invoice date",
    "0036": "Credit Note Value of Sale must be less or equal to the value of Sale in original invoice.",
    "0037": "Credit Note Value of ST Withheld must be less or equal to the value of ST Withheld in original invoice.",
    "0039": "For registered users, STWH invoice fields must be same as sale invoice",
    "0041": "Invoice number cannot be empty, please provide invoice number.",
    "0042": "Invoice date cannot be empty, please provide invoice date.",
    "0043": "Invoice date is not valid, please provide valid invoice date.",
    "0044": "HS Code cannot be empty, please provide HS Code",
    "0046": "Rate cannot be empty, please provide valid rate as per selected Sales Type.",
    "0050": "Please provide valid Sales Tax withheld. For sale type 'Cotton ginners', Sales Tax Withheld must be equal to Sales Tax or zero",
    "0052": "HS Code that does not match with provided sale type, Please provide valid HS Code against sale type",
    "0053": "Buyer Registration Type is invalid, please provide valid Buyer Registration Type",
    "0055": "Sales tax withheld cannot be empty or invalid format. Please provide valid sales tax withheld.",
    "0056": "Buyer does not exist in steel sector",
    "0057": "Reference invoice for debit/ credit note does not exists. Please provide valid Invoice Reference No.",
    "0058": "Buyer and Seller Registration number are same, this type of invoice is not allowed",
    "0064": "Credit note is already added to a invoice",
    "0067": "Sales Tax value of Debit Note is greater than original invoice's sales tax",
    "0068": "Sales Tax value of Credit Note is less than original invoice's sales tax according to the rate.",
    "0070": "User is not registered, STWH is allowed only for registered user",
    "0071": "Credit note allowed to add only for specific users",
    "0073": "Sale Origination Province of Supplier cannot be empty, please provide valid Sale Origination Province of Supplier.",
    "0074": "Destination of Supply cannot be empty, please provide valid Destination of Supply",
    "0077": "SRO/Schedule Number cannot be empty, please provide valid SRO/Schedule Number",
    "0078": "Item serial number cannot be empty, please provide valid item serial number",
    "0079": "If sales value is greater than 20,000 than rate 5% is not allowed",
    "0080": "Further Tax' cannot be empty, please provide valid Further Tax",
    "0081": "'Input Credit not Allowed' cannot be empty, please provide 'Input Credit not Allowed'",
    "0082": "The Seller is not registered for sales tax. Please provide a valid registration/NTN.",
    "0083": "Seller Reg No. doesn't match. Please provide valid Seller Registration Number",
    "0085": "Total Value of Sales is not provided, please provide valid Total Value of Sales (In case of PFAD only)",
    "0086": "You are not an EFS license holder who has imported Compressor Scrap in the last 12 months.",
    "0087": "Petroleum Levy rates not configured properly. Please update levy rates properly",
    "0088": "Invoice number is not valid, please provide valid invoice number in alphanumeric format. For example: Inv-001",
    "0089": "FED Charged cannot be empty, please provide valid FED Charged",
    "0090": "Fixed / notified value or Retail Price cannot be empty, please provide valid Fixed / notified value or Retail Price",
    "0091": "Extra tax must be empty.",
    "0092": "Purchase type cannot be empty, please provide valid purchase type",
    "0093": "Selected Sale is are not allowed to Manufacturer. Please select proper sale type",
    "0095": "Extra Tax cannot be empty, please provide valid extra tax",
    "0096": "For provided HS Code, only KWH UOM is allowed",
    "0097": "Please provide UOM in KG",
    "0098": "Quantity / Electricity Unit cannot be empty, please provide valid Quantity / Electricity Unit",
    "0099": "UOM is not valid. UOM must be according to given HS Code",
    "0100": "Registered user cannot add sale invoice. Only cotton ginner sale type is allowed for registered users.",
    "0101": "Sale type is not selected properly, please use Toll Manufacturing Sale Type for Steel Sector.",
    "0102": "The calculated sales tax not calculated as per 3rd schedule calculation formula",
    "0103": "Calculated tax not matched for potassium chlorate. Calculated value doesn't match according to potassium chlorate for sales potassium invoices.",
    "0104": "Calculated percentage of sales tax not matched. Calculation must be correct with respect to provided rate",
    "0105": "The calculated sales tax for the quantity is incorrect.",
    "0106": "The Buyer is not registered for sales tax. Please provide a valid registration/NTN.",
    "0107": "Buyer Reg No. doesn't match. Please provide valid Buyer Registration Number",
    "0108": "Seller Reg No. is not valid. Please provide valid Seller Registration Number/NTN",
    "0109": "Invoice type is not selected properly, please select proper invoice type",
    "0111": "Purchase type is not selected properly, please provide proper purchase type",
    "0113": 'Date is not in proper format, please provide date in "YYYY-MM-DD" format. For example: 2025-05-25',
    "0300": "Provided decimal value is not valid at the named field (Discount, Total Value, Fed Payable, Extra Tax, Further Tax, SalesTaxWithheldAtSource or Quantity) for the reported item",
    "0401": "Unauthorized access: Provided seller registration number is not 13 digits (CNIC) or 7 digits (NTN) or the authorized token does not exist against seller registration number",
    "0402": "Unauthorized access: Provided buyer registration number is not 13 digits (CNIC) or 7 digits (NTN) or the authorized token does not exist against buyer registration number",
}

UNKNOWN_DESCRIPTION = "Error code not listed in PRAL DI API spec v1.12 section 7"


def describe(code: str) -> str:
    return ERROR_CATALOGUE.get(str(code).strip(), UNKNOWN_DESCRIPTION)


def error_codes_in(pral_body: object) -> list[str]:
    """Every errorCode in a PRAL body (header first, then items), de-duplicated, in order."""
    codes: list[str] = []
    if not isinstance(pral_body, dict):
        return codes
    vr = pral_body.get("validationResponse")
    if not isinstance(vr, dict):
        return codes
    if vr.get("errorCode"):
        codes.append(str(vr["errorCode"]))
    for s in vr.get("invoiceStatuses") or []:
        if isinstance(s, dict) and s.get("errorCode"):
            c = str(s["errorCode"])
            if c not in codes:
                codes.append(c)
    return codes


def descriptions_for(pral_body: object) -> list[ErrorDescription]:
    return [ErrorDescription(code=c, description=describe(c)) for c in error_codes_in(pral_body)]
