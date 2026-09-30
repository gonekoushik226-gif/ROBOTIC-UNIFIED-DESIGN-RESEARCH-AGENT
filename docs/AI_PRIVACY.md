# Optional AI assistance and your privacy

RUDRA is complete without any AI service: it imports, extracts, answers, calculates and
checks provenance entirely on your computer. Optional AI assistance lets you connect one
external language service of your choice to help with wording. It never becomes the
source of your knowledge.

## Providers

| Provider | Connection |
|---|---|
| Anthropic | Messages API (`api.anthropic.com/v1/messages`) |
| OpenAI | Responses API (`api.openai.com/v1/responses`) |
| Google Gemini | Gemini API (`generativelanguage.googleapis.com`) |
| Mistral AI | Chat Completions API (`api.mistral.ai`) |

You use **your own account and API key** with the provider; RUDRA contains no key of its
own. The models offered are the ones the provider lists for your key. The provider's own
terms and data policy apply to what you send; the AI page links to them.

## Off by default

AI assistance is off after installation. On the **AI** page you choose a provider, add
your key and a model, and turn it on. Before it is turned on, RUDRA shows what will be
sent and asks for your agreement; without it nothing is enabled. **Turn off** disables it;
**Remove key** deletes the key.

## Your key

The key is stored by the **Windows Credential Manager** for your Windows account (entry
`RUDRA/ai/<provider>`), encrypted by Windows. It is never written to a RUDRA file, log,
report, diagnostic output or knowledge-base backup, and it is sent only to the provider
you chose, in the request header that provider requires — never in a web address. Error
messages from a provider are cleaned of anything that looks like a key before they are
shown.

## What is sent, and when

Nothing is sent until you press a button on an answer:

- **Interpret** — for a question RUDRA's own grammar did not understand. Only the text of
  your question is sent. The provider may return only a search request: one of RUDRA's
  fixed question types and a topic, which must be words from your question. RUDRA then
  runs that question against your own knowledge base; a provider that names a topic you
  did not write is refused.
- **Explain** — for an answer already shown. Only your question and the numbered
  statements of that answer are sent: no file names, paths, identifiers, other documents,
  the knowledge base or anything about your computer.

## What comes back

An explanation is checked sentence by sentence against the statements it was given:

- every sentence must cite the statements it relies on (`[1]`, `[2][3]`);
- a sentence without a valid citation, or containing a number (a value, a date, a
  quantity) that its cited statements do not contain, is **removed**, and the number of
  removed sentences is shown;
- if the provider says the statements are not enough, RUDRA says so and adds nothing.

What remains is shown under **Explanation — worded by <provider> from the statements
above, not a source**, next to the statements themselves. It is never stored as
knowledge, never becomes provenance, and is never presented as coming from your
documents.

## Offline

AI assistance needs an Internet connection. Without one, the buttons report that the
provider could not be reached; everything else in RUDRA keeps working.

## Tests

The connections are tested against recorded request and reply shapes for each provider,
with fake keys and no network access. They have not been exercised against the live
services by the automated tests.
