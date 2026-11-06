from .models import Brief

SAMPLE_BRIEFS: dict[str, Brief] = {
    "meridian": Brief(
        account="Meridian Mutual", region="US", segment="insurer", regulator="NAIC",
        problem=("Adjusters spend hours reading first-notice-of-loss emails, photos and police reports "
                 "to classify auto and property claims and decide severity. Simple claims queue behind "
                 "complex ones and cycle time keeps growing."),
        systems=["Guidewire ClaimCenter", "Salesforce"],
        timeline="POC live in 10 weeks, pilot with 2 claims teams",
        success="Auto-classify 60% of new claims and route severity correctly with adjuster sign-off"),
    "northbridge": Brief(
        account="Northbridge Bank", region="US", segment="retail bank", regulator="OCC",
        problem=("Compliance analysts manually review passports, utility bills and proof-of-address "
                 "documents during account onboarding. Reviews take days and quality varies between analysts."),
        systems=["Actimize", "Salesforce"],
        timeline="Decision on pilot by Q1, 8-week POC",
        success="Cut document review time by 40% with analyst-level extraction accuracy"),
    "atlas": Brief(
        account="Atlas Credit", region="US", segment="lender", regulator="CFPB",
        problem=("Underwriters assemble credit memos by hand from pay stubs, bank statements and bureau "
                 "reports, then check each loan against policy. Memo preparation delays decisions on "
                 "small-business loans."),
        systems=["nCino", "Equifax", "Encompass"],
        timeline="POC in 8 weeks before the summer lending peak",
        success="Reduce memo preparation time by 35% and explain every policy check to the underwriter"),
    "keystone": Brief(
        account="Keystone Securities", region="US", segment="capital markets", regulator="SEC",
        problem=("Regulatory reports (transaction and position reporting) are stitched together from trade, "
                 "reference and risk data in spreadsheets. Reconciliation breaks surface late and reports "
                 "are filed at the deadline."),
        systems=["Snowflake", "Murex"],
        timeline="12-week POC aligned to the next filing cycle",
        success="Generate the report automatically with reconciliation exceptions flagged before the deadline"),
    "harbor": Brief(
        account="Harbor Trust Bank", region="US", segment="retail bank", regulator="OCC",
        problem=("Fraud analysts work thousands of daily card and wire alerts, most of them false positives. "
                 "High-risk alerts wait in the queue while analysts re-investigate the same patterns."),
        systems=["FICO Falcon", "Kafka"],
        timeline="6-week POC",
        success="Prioritise alerts so analysts review high-risk cases first and cut false-positive review load by 30%"),
}
