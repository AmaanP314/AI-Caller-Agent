Stable docker image: v1.7, v2.4, v2.5

[2025-11-12T01:40:26.109845934Z] [LLM→Queue] Sentence: Hi, this is Jane calling from Nationwide Screening...
[2025-11-12T01:40:26.109857469Z] [Queue→TTS] Synthesizing for pcm16k: Hi, this is Jane calling from Nationwide Screening...
[2025-11-12T01:40:26.222996647Z] [LLM Producer] Sending sentinel to TTS.
[2025-11-12T01:40:26.223143967Z] [call-1762911625] LLM finished naturally, waiting for TTS.
[2025-11-12T01:40:27.965667556Z] [TTS] Generated pcm16k: 111200 bytes @ 16000Hz
[2025-11-12T01:40:27.965796519Z] [TTS→Audio Queue] Chunk ready (111200 bytes)
[2025-11-12T01:40:28.46454977Z] [TTS Consumer] Task finished.
[2025-11-12T01:40:35.850869812Z] [call-1762911625] 🎤 User speaking (energy: 0.0300)
[2025-11-12T01:40:38.390839891Z] [call-1762911625] 🎯 Transcribing 81920 bytes (26 chunks, 832ms)
[2025-11-12T01:40:38.950431373Z] [call-1762911625] 📝 User: 'in the world.' # STT inaccurate here, I said hello jane, but it is not a big deal, I may have spoke it unclearly, but what is important here is that I said this in the midst of agent's speech and still the agent didn't stop
[2025-11-12T01:40:39.272274964Z] [LLM→Queue] Sentence: That's a very grand title!...
[2025-11-12T01:40:39.36048236Z] [LLM Producer] Sending sentinel to TTS.
[2025-11-12T01:40:39.360617739Z] [call-1762911625] LLM finished naturally, waiting for TTS.
[2025-11-12T01:40:39.406123897Z] [TTS] Generated pcm16k: 64000 bytes @ 16000Hz
[2025-11-12T01:40:39.40626679Z] [TTS→Audio Queue] Chunk ready (64000 bytes)
[2025-11-12T01:40:39.406283532Z] [Queue→TTS] Synthesizing for pcm16k: While I appreciate the enthusiasm, I just need you...
[2025-11-12T01:40:39.67242069Z] [Queue→TTS] Received sentinel, ending synthesis
[2025-11-12T01:40:39.672422526Z] [TTS Consumer] Task finished.
[2025-11-12T01:40:39.889422145Z] [call-1762911625] 🎤 User speaking (energy: 0.0011)
[2025-11-12T01:40:41.348675873Z] [call-1762911625] ⏭️ Speech too short (1 chunks, 32ms < 300ms), ignoring # (This is exactly what's previously causing the STT model hallucination in our previous code version, here, though I was completely silence here, VAD sometimes detect it as speech and send it to STT, because the speech is really short and silence, STT model hallucinate and transcribe it to "Thank you" or "bye", this transcribe then fed in to LLM and its response to TTS)
[2025-11-12T01:40:46.830212559Z] [call-1762911625] 🎤 User speaking (energy: 0.0056)
[2025-11-12T01:40:48.311875117Z] [call-1762911625] ⏭️ Speech too short (1 chunks, 32ms < 300ms), ignoring
[2025-11-12T01:41:04.951010369Z] [call-1762911625] 🎤 User speaking (energy: 0.0319)
[2025-11-12T01:41:07.52880245Z] [call-1762911625] 🎯 Transcribing 83968 bytes (36 chunks, 1152ms)
[2025-11-12T01:41:07.528819829Z] [STT] Transcribing 83968 bytes of raw pcm16k
[2025-11-12T01:41:07.761459841Z] [call-1762911625] 📝 User: 'Well I am John Anderson.'
[2025-11-12T01:41:07.762990043Z] [call-1762911625] 🎤 User speaking (energy: 0.0027)
[2025-11-12T01:41:08.101104331Z] [Tool] Updated: {'patient_name': 'John Anderson'}
[2025-11-12T01:41:08.590772159Z] [LLM→Queue] Sentence: Thank you, Mr. Anderson. I have your name down.
[2025-11-12T01:41:08.591702393Z] [call-1762911625] LLM finished naturally, waiting for TTS.
[2025-11-12T01:41:08.939705349Z] [TTS] Generated pcm16k: 432800 bytes @ 16000Hz
[2025-11-12T01:41:08.939716733Z] [TTS→Audio Queue] Chunk ready (432800 bytes)
[2025-11-12T01:41:08.939718445Z] [Queue→TTS] Received sentinel, ending synthesis
[2025-11-12T01:41:08.939719968Z] [TTS Consumer] Task finished.
[2025-11-12T01:41:09.129875035Z] [call-1762911625] ⏭️ Speech too short (3 chunks, 96ms < 300ms), ignoring
[2025-11-12T01:41:27.948684679Z] [call-1762911625] 🎤 User speaking (energy: 0.0589)
[2025-11-12T01:41:29.509860982Z] [call-1762911625] ⏭️ Speech too short (4 chunks, 128ms < 300ms), ignoring
[2025-11-12T01:41:33.512077204Z] [call-1762911625] 🎤 User speaking (energy: 0.0026)
[2025-11-12T01:41:34.989786791Z] [call-1762911625] ⏭️ Speech too short (1 chunks, 32ms < 300ms), ignoring
[2025-11-12T01:41:35.211676638Z] [call-1762911625] 🎤 User speaking (energy: 0.0241)
[2025-11-12T01:41:39.589735469Z] [call-1762911625] 🎯 Transcribing 141312 bytes (79 chunks, 2528ms)
[2025-11-12T01:41:39.589875778Z] [STT] Transcribing 141312 bytes of raw pcm16k
[2025-11-12T01:41:39.872842768Z] [call-1762911625] 📝 User: 'No, not particularly, I don't have any conditions like that.'
[2025-11-12T01:41:40.317427623Z] [Tool] Updated: {'medical_conditions': []}
[2025-11-12T01:41:40.70942663Z] [LLM→Queue] Sentence: That's good to hear.

[2025-11-12T04:00:25.140581551Z] [call-1762920024] 🔗 WebSocket connected
[2025-11-12T04:00:25.140865583Z] [call-1762920024] 🎤 Sending greeting...
[2025-11-12T04:00:25.141136973Z] [32mINFO[0m: connection open
[2025-11-12T04:00:25.141291346Z] [call-1762920024] 🔊 Agent is now speaking (flag set)
[2025-11-12T04:00:25.141295504Z] [call-1762920024] 📊 VAD Config:
[2025-11-12T04:00:25.141297271Z] Silence timeout: 1500ms (46 chunks)
[2025-11-12T04:00:25.141298782Z] Min speech duration: 300ms (9 chunks)
[2025-11-12T04:00:25.141300504Z] Barge-in threshold: 3 chunks
[2025-11-12T04:00:25.141302211Z] VAD threshold: 0.5
[2025-11-12T04:00:25.141303903Z] Energy threshold: 0.005
[2025-11-12T04:00:25.419990954Z] [call-1762920024] 🔇 Low energy: 0.0000 < 0.0075
[2025-11-12T04:00:25.532023596Z] [LLM→Queue] Sentence: Hi, this is Jane calling from Nationwide Screening...
[2025-11-12T04:00:27.03973072Z] [TTS] Generated pcm16k: 111200 bytes @ 16000Hz
[2025-11-12T04:00:27.039902737Z] [TTS→Audio Queue] Chunk ready (111200 bytes)
[2025-11-12T04:00:27.642369537Z] [TTS] Generated pcm16k: 112000 bytes @ 16000Hz
[2025-11-12T04:00:27.64252171Z] [TTS→Audio Queue] Chunk ready (112000 bytes)
[2025-11-12T04:00:27.64265942Z] [Queue→TTS] Received sentinel, ending synthesis
[2025-11-12T04:00:27.642694721Z] [TTS Consumer] Task finished.
[2025-11-12T04:00:32.35119734Z] [call-1762920024] VAD: 🤐 energy=0.0120
[2025-11-12T04:00:32.387511732Z] [call-1762920024] VAD: 🗣️ energy=0.0568 (I interrupted here in the midst of agent's speech its only 5 seconds when agent starts speaking, the entire speech was around 22 seconds)
[2025-11-12T04:00:32.387524311Z] [call-1762920024] 🎤 User speaking (energy: 0.0568)
[2025-11-12T04:00:32.391866959Z] [call-1762920024] VAD: 🗣️ energy=0.0841
[2025-11-12T04:00:32.392896315Z] [call-1762920024] VAD: 🗣️ energy=0.0418
[2025-11-12T04:00:32.394133478Z] [call-1762920024] VAD: 🗣️ energy=0.0471
[2025-11-12T04:00:33.179901836Z] [call-1762920024] 🔇 Low energy: 0.0001 < 0.0075
[2025-11-12T04:00:33.219878363Z] [call-1762920024] 🔇 Low energy: 0.0000 < 0.0075
...
[2025-11-12T04:00:34.634263012Z] [call-1762920024] 🎯 Transcribing 75776 bytes (15 chunks, 480ms)
[2025-11-12T04:00:35.238020388Z] [call-1762920024] 📝 User: 'hello Jane'
[2025-11-12T04:00:35.239040443Z] [call-1762920024] 🔇 Low energy: 0.0000 < 0.0075
[2025-11-12T04:00:35.242566027Z] [call-1762920024] 🔊 Agent is now speaking (flag set)
[2025-11-12T04:00:35.740580058Z] [call-1762920024] 🔇 Low energy: 0.0000 < 0.0075
[2025-11-12T04:00:35.753375097Z] [LLM→Queue] Sentence: I understand you might have some questions, but to...
[2025-11-12T04:00:35.740580058Z] [call-1762920024] 🔇 Low energy: 0.0000 < 0.0075
[2025-11-12T04:00:35.753375097Z] [LLM→Queue] Sentence: I understand you might have some questions, but to...
[2025-11-12T04:00:35.753383745Z] [Queue→TTS] Synthesizing for pcm16k: I understand you might have some questions, but to...
[2025-11-12T04:00:35.762340465Z] [LLM Producer] Sending sentinel to TTS.
[2025-11-12T04:00:35.762618034Z] [call-1762920024] LLM finished naturally, waiting for TTS.
[2025-11-12T04:00:35.779861807Z] [call-1762920024] 🔇 Low energy: 0.0000 < 0.0075
[2025-11-12T04:00:35.984267127Z] [TTS] Generated pcm16k: 280000 bytes @ 16000Hz
[2025-11-12T04:00:35.984384728Z] [TTS→Audio Queue] Chunk ready (280000 bytes)
[2025-11-12T04:00:35.984387886Z] [Queue→TTS] Received sentinel, ending synthesis
[2025-11-12T04:00:35.984487045Z] [TTS Consumer] Task finished.
