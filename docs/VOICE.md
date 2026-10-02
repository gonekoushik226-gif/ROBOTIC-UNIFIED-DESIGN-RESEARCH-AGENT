# Speaking to RUDRA

Click the microphone button (**Speak** beside the question, or **Test microphone** in
Settings), talk, and stop talking. RUDRA listens until you have been quiet for about a
second - or until you click the button again - then works out the words and puts them in the
text box. You read them, change anything, and press **Ask**. Speaking never sends anything
by itself, and a spoken request is exactly as safe as a typed one: voice never authorizes an
action (a risky step still needs your typed confirmation).

Everything happens **on your computer, offline**. There is no account, no cloud service and no
network connection. Microphone audio is kept in memory only while it is being recognized: it is
never written to disk and never sent anywhere. RUDRA does not listen unless you have clicked.

## What it uses

| Piece | What it is | Version | License |
|---|---|---|---|
| whisper.cpp | the program that runs the speech model (`whisper-cli.exe`) | v1.9.4, release build b5130 | MIT |
| OpenAI Whisper small.en | the speech model: English, 5-bit quantized, 190 MB | ggml file from huggingface.co/ggerganov/whisper.cpp | MIT |
| Silero VAD v5.1.2 | tells speech from non-speech, so silence and noise are not "heard" | ggml file from huggingface.co/ggml-org/whisper-vad | MIT |

They live in the `speech` folder beside the program, installed by the installer, with their
license texts in `licenses`. [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) has the exact
versions and hashes. From a source checkout, `python windows\fetch_speech.py` downloads them once
(each file pinned by URL, size and SHA-256; anything that does not match is refused); nothing is
ever downloaded while RUDRA runs. The Windows voices that `voice --say` uses for speech output
are Windows' own.

Each time you speak, RUDRA starts the program once with the audio on its standard input, with no
window and at low priority; when it has printed the words it exits and its memory (about
0.5 GB) is released. That is why speech costs nothing while you are not using it.

## How accurate is it

Windows' built-in dictation, which RUDRA used before, turned *"My name is Kaushik."* into
*"Like name is go seek"*. To measure the replacement fairly, the same 96 recordings were read by
both: 16 phrases (names, ordinary sentences, technical questions, short commands) spoken by
three Windows voices (David and Zira, US; Hazel, UK), each clean and with a quiet signal, room
noise and a narrow band, as a poor laptop microphone gives. "Words wrong" counts substitutions,
deletions and insertions against the phrase, ignoring capitals, punctuation and digits-for-words.

| | Words wrong | Recordings read perfectly | Names | Commands | Sentences | Technical |
|---|---|---|---|---|---|---|
| Windows dictation (before) | **38.2%** | 18 of 96 | 53.2% | 60.3% | 21.6% | 37.6% |
| Whisper base.en (60 MB), whole 30 s window | 3.6% | 74 of 96 | 15.9% | 7.7% | 0.5% | 0.0% |
| Whisper small.en (190 MB), whole 30 s window | 2.6% | 80 of 96 | 15.9% | 0.0% | 0.0% | 0.0% |
| **small.en as RUDRA runs it** (window sized to the speech, voice-activity model, RUDRA's own words) | **3.0%** | 78 of 96 | 16.7% | 2.6% | 0.0% | 0.0% |
| ... and with the names added under Settings | 2.5% | 81 of 96 | 13.5% | 2.6% | 0.0% | 0.0% |

(The difference between the whole-window and "as RUDRA runs it" rows is 3 words out of 756, mostly
the spelling of names, and it changes with the subset: on a controlled comparison of 48 of the
recordings the two settings scored the same, 1.5% each (6 words of 396). Sizing the window to the
speech is what makes recognition about three times faster; see below.)

Examples, David's voice, clean (before → now): *"What is Ohm's law?"* → *"What is always a
lot"* → *"What is Ohm's law?"*; *"Calculate the current when the voltage is twelve volts…"* →
*"…the voltages 12 volts in the resistances five owns"* → *"…the voltage is 12 volts and the
resistance is 5 ohms."*

What is left is almost all **names**: Whisper writes an unfamiliar name the way it sounds
(*"Kaushik"* came back as *"Koshik"* in some recordings, *"Krishnamurthy"* as *"Krishnamurti"*).
Add names and terms under **Settings > Voice > Words to spell as written** and RUDRA tells the
recognizer how you write them. In the controlled test set, the configured name hints reduced
the name-category word error rate from 16.7% to 13.5%; individual names and accents still vary.

Limits of this measurement, stated plainly: the voices are synthetic (clean, regular, easier
than a person), there are three of them, and the "noisy" copies are simulated. A real person's
accent, distance from the microphone and room will give different numbers. A five-phrase live
microphone check was also attempted on this machine. It did not establish reliable live accuracy:
`My name is Kaushik.` returned `My.` on the first try; with a name hint the next try returned
`My name is Kaushik. My name is Kaushik. What is OMSLA?`; `What is Ohm's law?` returned
`Ssum say nothing at all.`; `Calculate the current when the voltage is twelve volts and the
resistance is five ohms.` returned no words; silence returned no words. The captures did not
isolate the prompted utterances consistently (one transcript combined multiple phrases), so this
is a failed live-microphone check, not a valid accuracy benchmark. The controlled synthetic
recordings below cannot substitute for a clean human microphone test.

## How long it takes

With the speech-length window and four CPU threads, the measured recognition wall time on this
machine (Intel i3-1215U, 8 GB RAM, no GPU) was about **1.2 seconds** for a typical two-second
utterance, **2.0 seconds** for a 5.5-second technical utterance, and **3.7 seconds** for an
11-second passage. These are measured runs, not a guarantee; the first cold run took 6.5 seconds.

## When it is not sure, or cannot hear

| What happens | What you see |
|---|---|
| The model was unsure (mean token probability under 0.60, or any token under 0.50) | The words are inserted as usual and the status line says **CHECK THE WORDS** |
| Nothing was said, or only noise | Nothing is inserted: *"RUDRA did not hear anything. Try again and speak soon after pressing Speak."* RUDRA never invents words for silence |
| The microphone is muted or Windows blocks it | *"Windows is giving RUDRA silence from the microphone. Check that it is not muted and that Windows allows desktop apps to use it (Settings > Privacy & security > Microphone)."* |
| No microphone, or another program is using it | *"RUDRA could not find a microphone…"* / *"The microphone is being used by another program…"* |
| You click the button while it is working out the words | It stops and inserts nothing |
| It takes far too long | It is stopped after a limit that grows with the length of the speech: *"Speech recognition took too long and was stopped."* |
| The speech files are missing or damaged | *"A speech file (…) is missing or damaged."* with the way to repair it |
| Not enough free memory | *"There was not enough free memory to recognise speech. Close some programs and try again."* |
| Security software blocks the speech program | *"Windows blocked RUDRA's speech program. Allow the speech folder in your security software."* |

Typing always still works.

## On the command line

```powershell
RUDRA-CLI voice --listen                     # one utterance from the microphone (at most 10 s)
RUDRA-CLI voice --audio request.wav --dry-run
RUDRA-CLI voice --say "Open Calculator." --audio new.wav   # speech output (Windows' voice)
RUDRA-CLI voice --audio request.wav --json   # the transcript, its confidence and the recognizer
```

A WAV file of any sample rate and up to 10 MiB is read; the transcript, then the same
pipeline as typed text. The transcript's `confidence` is the model's own mean word probability,
not a RUDRA score.

## Resources, on an Intel i3-1215U with 8 GB of RAM and no GPU

The quantized model file is 190,098,681 bytes (about 181 MiB); the CPU executable and its DLLs
add about 9 MB, and the voice-activity model is under 1 MB. In observed inference runs the process
used roughly **335–357 MB peak working memory**, below the conservative 0.5 GB guidance above.
The expected tradeoff is a larger installer and a short CPU burst while recognizing; no GPU,
Python package, account, or runtime download is needed. These measurements cover the recognizer
process, not the complete desktop application's total memory.

## For developers

`app/voice/engine.py` runs the program and reads its JSON result; `capture.py` listens to the
microphone (`winmm` through `ctypes`) and decides when you have finished; `words.py` keeps your
words; `speech.py` is the API (`listen`, `recognize_file`, `synthesize`, `vocabulary_prompt`).
`RUDRA_SPEECH_DIR` points at another `speech` folder. The tests use a fake program and a fake
`winmm`; the tests that need the real recogniser skip, with their reason, where `speech\` is not
installed.
