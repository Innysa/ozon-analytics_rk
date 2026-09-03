from pydantic import BaseModel


class StoreAISettingsOut(BaseModel):
    brand_name: str | None = None
    tone_of_voice: str
    customer_address_form: str
    reply_length: str
    use_emoji: bool
    signature: str | None = None
    forbidden_words: str | None = None
    allowed_promises: str | None = None
    negative_review_rules: str | None = None
    warranty_info: str | None = None
    return_policy_info: str | None = None
    support_contacts: str | None = None
    product_facts: str | None = None

    model_config = {"from_attributes": True}


class StoreAISettingsUpdate(BaseModel):
    brand_name: str | None = None
    tone_of_voice: str | None = None
    customer_address_form: str | None = None
    reply_length: str | None = None
    use_emoji: bool | None = None
    signature: str | None = None
    forbidden_words: str | None = None
    allowed_promises: str | None = None
    negative_review_rules: str | None = None
    warranty_info: str | None = None
    return_policy_info: str | None = None
    support_contacts: str | None = None
    product_facts: str | None = None
