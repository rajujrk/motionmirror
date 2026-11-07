# ADR 0006: Dataset licence policy

**Status:** accepted by the product owner, 2026-11-06.

**Evidence.** The Kaggle API reports `yasaminjafarian/tiktokdataset` as `Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)`,
53,288,866,606 bytes. A mirror of the project page asks for research use only. UBC Fashion's official page states no licence.
HumanVid's Hugging Face terms are gated. Three Hugging Face dance datasets show no licence or source in their metadata.

**Decision.** The stricter statement applies. TikTok data can produce a clearly labelled research adapter only, which can
never be promoted to the production default. Production uses the base model until consented footage is supplied. Datasets
with no verifiable licence are blocked until a human review is recorded. The Hugging Face sets without provenance are rejected.

**Consequences.** The licence gate is enforced in the dataset tools and in adapter registration. The real people in dataset
clips have not individually consented, which is one more reason research adapters stay out of production.
