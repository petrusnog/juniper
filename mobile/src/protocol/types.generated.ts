/* GENERATED from docs/protocol/schema.json by scripts/gen-protocol-types.js. Do not edit. */

export type JuniperClientBrainProtocol =
  | Hello
  | Pong
  | ChatSend
  | ChatCancel
  | SessionNew
  | SessionResume
  | SessionList
  | (
      | MemoryListRequest
      | MemorySearchRequest
      | MemoryForgetRequest
      | MemoryExportRequest
      | MemoryProfileGetRequest
      | MemoryProfileSetRequest
      | MemoryProfileForgetRequest
    )
  | Register
  | Result
  | ToolsRequest
  | VoiceStart
  | VoiceAudio
  | VoiceText
  | VoiceInterrupt
  | VoiceRoute
  | VoiceEnd
  | Welcome
  | Ping
  | Error1
  | ChatStarted
  | ChatToken
  | ChatEnd
  | SessionState
  | SessionListed
  | SessionDetached
  | MemoryResult
  | Registered
  | Invoke
  | ToolsListed
  | Trace1
  | VoiceReady
  | VoiceState
  | VoiceTranscript
  | VoiceSay
  | VoiceFallback;
export type DeviceId = string;
export type DeviceName = string | null;
export type Kind = string;
export type Version = string;
export type ProtocolVersion = number;
export type Latest = boolean | null;
export type SessionId = string | null;
export type Trace = boolean | null;
export type Type = 'hello';
export type Type1 = 'pong';
export type Id = string;
export type Text = string;
export type Type2 = 'chat.send';
export type TurnId = string;
export type Type3 = 'chat.cancel';
export type Id1 = string;
export type Type4 = 'session.new';
export type Id2 = string;
export type Latest1 = boolean | null;
export type SessionId1 = string | null;
export type Type5 = 'session.resume';
export type Id3 = string;
export type Limit = number | null;
export type Type6 = 'session.list';
export type All = boolean | null;
export type Type7 = string | null;
export type Id4 = string;
export type Op = 'list';
export type Type8 = 'memory.request';
export type Limit1 = number | null;
export type Query = string;
export type Threshold = number | null;
export type Id5 = string;
export type Op1 = 'search';
export type Type9 = 'memory.request';
export type Id6 = string;
export type Id7 = string;
export type Op2 = 'forget';
export type Type10 = 'memory.request';
export type Vectors = boolean | null;
export type Id8 = string;
export type Op3 = 'export';
export type Type11 = 'memory.request';
export type Id9 = string;
export type Op4 = 'profile.get';
export type Type12 = 'memory.request';
export type BirthDate = string | null;
export type Gender = string | null;
export type Name = string | null;
export type Nationality = string | null;
export type Text1 = string;
export type Id10 = string;
export type Op5 = 'profile.set';
export type Type13 = 'memory.request';
export type Id11 = string;
export type Op6 = 'profile.forget';
export type Type14 = 'memory.request';
export type Description = string;
export type Name1 = string;
export type Tier = string;
export type Tools = ToolSpec[];
export type Type15 = 'register';
export type CallId = string;
export type Error = string | null;
export type Metadata = {
  [k: string]: unknown;
} | null;
export type Output = string | null;
export type Success = boolean;
export type Type16 = 'result';
export type Id12 = string;
export type Type17 = 'tools.request';
export type Channels = 1;
export type Encoding = 'pcm_s16le';
export type SampleRate = number;
export type Id13 = string;
export type LocalStt = boolean | null;
export type LocalTts = boolean | null;
export type Route = ('speaker' | 'headset') | null;
export type Type18 = 'voice.start';
export type Wake = boolean | null;
export type Data = string;
export type Last = boolean | null;
export type Seq = number;
export type TurnId1 = string | null;
export type Type19 = 'voice.audio';
export type Id14 = string;
export type Text2 = string;
export type Type20 = 'voice.text';
export type Type21 = 'voice.interrupt';
export type Route1 = 'speaker' | 'headset';
export type Type22 = 'voice.route';
export type Reason = 'client' | 'command' | 'silence' | 'error' | 'server' | 'playback';
export type Type23 = 'voice.end';
export type HeartbeatSeconds = number;
export type ProtocolVersion1 = number;
export type ServerVersion = string;
export type Id15 = string;
export type Resumed = boolean;
export type Turns = number;
export type Type24 = 'welcome';
export type Type25 = 'ping';
export type Code =
  | 'unauthorized'
  | 'unsupported_version'
  | 'bad_request'
  | 'turn_in_progress'
  | 'session_not_found'
  | 'llm_auth'
  | 'llm_rate_limit'
  | 'llm_unavailable'
  | 'llm_timeout'
  | 'llm_error'
  | 'memory_unavailable'
  | 'not_found'
  | 'executor_unavailable'
  | 'voice_unavailable'
  | 'voice_busy'
  | 'internal';
export type Message = string;
export type ReplyTo = string | null;
export type Retryable = boolean;
export type Type26 = 'error';
export type ReplyTo1 = string;
export type SessionId2 = string;
export type TurnId2 = string;
export type Type27 = 'chat.started';
export type Text3 = string;
export type TurnId3 = string;
export type Type28 = 'chat.token';
export type Finish = 'ok' | 'cancelled' | 'max_iterations' | 'error';
export type Timing = {
  [k: string]: unknown;
} | null;
export type TurnId4 = string;
export type Type29 = 'chat.end';
export type Usage = {
  [k: string]: unknown;
} | null;
export type History = HistoryItem[] | null;
export type Role = string;
export type Text4 = string;
export type Note = string;
export type ReplyTo2 = string;
export type SessionId3 = string;
export type Turns1 = number;
export type Type30 = 'session.state';
export type ReplyTo3 = string;
export type Id16 = string;
export type Preview = string;
export type Turns2 = number;
export type UpdatedAt = string;
export type Sessions = SessionSummary[];
export type Type31 = 'session.listed';
export type Reason1 = string;
export type SessionId4 = string;
export type Type32 = 'session.detached';
export type Data1 = {
  [k: string]: unknown;
} | null;
export type Ok = boolean;
export type ReplyTo4 = string;
export type Type33 = 'memory.result';
export type Tools1 = ToolSpec[];
export type Type34 = 'registered';
export type CallId1 = string;
export type Tier1 = string | null;
export type TimeoutMs = number;
export type ToolName = string;
export type Type35 = 'invoke';
export type ReplyTo5 = string;
export type Description1 = string;
export type Locality = string;
export type Name2 = string;
export type Tier2 = string;
export type Tools2 = ToolInfo[];
export type Type36 = 'tools.listed';
export type At = string;
export type Layer = string;
export type Text5 = string;
export type Type37 = 'trace';
export type ReplyTo6 = string;
export type Stt = 'cloud' | 'local';
export type Tts = 'cloud' | 'local';
export type Type38 = 'voice.ready';
export type State = 'listening' | 'thinking' | 'speaking' | 'idle';
export type TurnId5 = string | null;
export type Type39 = 'voice.state';
export type Final = boolean;
export type Text6 = string;
export type Type40 = 'voice.transcript';
export type Last1 = boolean | null;
export type Text7 = string;
export type TurnId6 = string;
export type Type41 = 'voice.say';
export type Component = 'stt' | 'tts';
export type Mode = 'local' | 'cloud';
export type Reason2 = 'quota' | 'budget' | 'error' | 'recovered';
export type Type42 = 'voice.fallback';

export interface Hello {
  client: ClientInfo;
  protocol_version: ProtocolVersion;
  resume?: Resume | null;
  trace?: Trace;
  type?: Type;
}
export interface ClientInfo {
  device_id: DeviceId;
  device_name?: DeviceName;
  kind: Kind;
  version: Version;
}
/**
 * Retomada da conversa: `session_id` ou `latest`.
 */
export interface Resume {
  latest?: Latest;
  session_id?: SessionId;
}
export interface Pong {
  type?: Type1;
}
export interface ChatSend {
  id: Id;
  text: Text;
  type?: Type2;
}
export interface ChatCancel {
  turn_id: TurnId;
  type?: Type3;
}
export interface SessionNew {
  id: Id1;
  type?: Type4;
}
export interface SessionResume {
  id: Id2;
  latest?: Latest1;
  session_id?: SessionId1;
  type?: Type5;
}
export interface SessionList {
  id: Id3;
  limit?: Limit;
  type?: Type6;
}
export interface MemoryListRequest {
  args?: MemoryListArgs;
  id: Id4;
  op?: Op;
  type?: Type8;
}
export interface MemoryListArgs {
  all?: All;
  type?: Type7;
}
export interface MemorySearchRequest {
  args: MemorySearchArgs;
  id: Id5;
  op?: Op1;
  type?: Type9;
}
export interface MemorySearchArgs {
  limit?: Limit1;
  query: Query;
  threshold?: Threshold;
}
export interface MemoryForgetRequest {
  args: MemoryForgetArgs;
  id: Id7;
  op?: Op2;
  type?: Type10;
}
export interface MemoryForgetArgs {
  id: Id6;
}
export interface MemoryExportRequest {
  args?: MemoryExportArgs;
  id: Id8;
  op?: Op3;
  type?: Type11;
}
export interface MemoryExportArgs {
  vectors?: Vectors;
}
export interface MemoryProfileGetRequest {
  args?: MemoryNoArgs;
  id: Id9;
  op?: Op4;
  type?: Type12;
}
/**
 * Operações sem argumentos (`profile.get`, `profile.forget`).
 */
export interface MemoryNoArgs {}
export interface MemoryProfileSetRequest {
  args: MemoryProfileSetArgs;
  id: Id10;
  op?: Op5;
  type?: Type13;
}
export interface MemoryProfileSetArgs {
  birth_date?: BirthDate;
  gender?: Gender;
  name?: Name;
  nationality?: Nationality;
  text: Text1;
}
export interface MemoryProfileForgetRequest {
  args?: MemoryNoArgs;
  id: Id11;
  op?: Op6;
  type?: Type14;
}
export interface Register {
  tools: Tools;
  type?: Type15;
}
export interface ToolSpec {
  description: Description;
  name: Name1;
  parameters?: Parameters;
  tier: Tier;
}
export interface Parameters {
  [k: string]: unknown;
}
export interface Result {
  call_id: CallId;
  error?: Error;
  metadata?: Metadata;
  output?: Output;
  success: Success;
  type?: Type16;
}
export interface ToolsRequest {
  id: Id12;
  type?: Type17;
}
/**
 * Abre a sessão de voz da conexão. `format` descreve o áudio que o microfone vai mandar.
 */
export interface VoiceStart {
  format: VoiceFormat;
  id: Id13;
  local_stt?: LocalStt;
  local_tts?: LocalTts;
  route?: Route;
  type?: Type18;
  wake?: Wake;
}
/**
 * Formato do áudio: PCM 16 bits little-endian, mono. A taxa vem de quem produz o áudio.
 */
export interface VoiceFormat {
  channels?: Channels;
  encoding?: Encoding;
  sample_rate: SampleRate;
}
/**
 * Um pedaço de áudio, nos dois sentidos: o microfone (cliente → servidor, sem `turn_id`) e a
 * voz da Juniper (servidor → cliente, com o `turn_id` do turno que ela está falando).
 */
export interface VoiceAudio {
  data: Data;
  last?: Last;
  seq: Seq;
  turn_id?: TurnId1;
  type?: Type19;
}
/**
 * Fala já transcrita no aparelho (fallback de STT): vira um turno falado.
 */
export interface VoiceText {
  id: Id14;
  text: Text2;
  type?: Type20;
}
export interface VoiceInterrupt {
  type?: Type21;
}
/**
 * A saída de áudio mudou no meio da conversa (o fone foi ligado ou desligado).
 */
export interface VoiceRoute {
  route: Route1;
  type?: Type22;
}
/**
 * Encerra a sessão de voz, de qualquer lado.
 */
export interface VoiceEnd {
  reason?: Reason;
  type?: Type23;
}
export interface Welcome {
  heartbeat_seconds: HeartbeatSeconds;
  protocol_version: ProtocolVersion1;
  server_version: ServerVersion;
  session: WelcomeSession;
  type?: Type24;
}
export interface WelcomeSession {
  id: Id15;
  resumed: Resumed;
  turns: Turns;
}
export interface Ping {
  type?: Type25;
}
export interface Error1 {
  code: Code;
  message: Message;
  reply_to?: ReplyTo;
  retryable: Retryable;
  type?: Type26;
}
export interface ChatStarted {
  reply_to: ReplyTo1;
  session_id: SessionId2;
  turn_id: TurnId2;
  type?: Type27;
}
export interface ChatToken {
  text: Text3;
  turn_id: TurnId3;
  type?: Type28;
}
export interface ChatEnd {
  finish: Finish;
  timing?: Timing;
  turn_id: TurnId4;
  type?: Type29;
  usage?: Usage;
}
export interface SessionState {
  history?: History;
  note?: Note;
  reply_to: ReplyTo2;
  session_id: SessionId3;
  turns: Turns1;
  type?: Type30;
}
export interface HistoryItem {
  role: Role;
  text: Text4;
}
export interface SessionListed {
  reply_to: ReplyTo3;
  sessions: Sessions;
  type?: Type31;
}
export interface SessionSummary {
  id: Id16;
  preview: Preview;
  turns: Turns2;
  updated_at: UpdatedAt;
}
export interface SessionDetached {
  reason: Reason1;
  session_id: SessionId4;
  type?: Type32;
}
export interface MemoryResult {
  data?: Data1;
  ok: Ok;
  reply_to: ReplyTo4;
  type?: Type33;
}
export interface Registered {
  tools: Tools1;
  type?: Type34;
}
export interface Invoke {
  arguments?: Arguments;
  call_id: CallId1;
  tier?: Tier1;
  timeout_ms: TimeoutMs;
  tool_name: ToolName;
  type?: Type35;
}
export interface Arguments {
  [k: string]: unknown;
}
export interface ToolsListed {
  reply_to: ReplyTo5;
  tools: Tools2;
  type?: Type36;
}
export interface ToolInfo {
  description: Description1;
  locality: Locality;
  name: Name2;
  tier: Tier2;
}
/**
 * Um passo que o servidor deu (só vem se o `hello` pediu e o servidor permite).
 */
export interface Trace1 {
  at: At;
  layer: Layer;
  text: Text5;
  type?: Type37;
}
export interface VoiceReady {
  input: VoiceFormat;
  output: VoiceFormat;
  reply_to: ReplyTo6;
  stt: Stt;
  tts: Tts;
  type?: Type38;
}
export interface VoiceState {
  state: State;
  turn_id?: TurnId5;
  type?: Type39;
}
/**
 * O que o cérebro entendeu que o Petrus disse (a legenda dele).
 */
export interface VoiceTranscript {
  final?: Final;
  text: Text6;
  type?: Type40;
}
/**
 * Fallback de TTS: o aparelho fala o `text` com a voz dele.
 */
export interface VoiceSay {
  last?: Last1;
  text: Text7;
  turn_id: TurnId6;
  type?: Type41;
}
/**
 * Troca de provedor: `local` = o aparelho assume; `cloud` = a nuvem voltou.
 */
export interface VoiceFallback {
  component: Component;
  mode: Mode;
  reason: Reason2;
  type?: Type42;
}

export const PROTOCOL_VERSION = 1;
export const MAX_MESSAGE_BYTES = 1048576;
/** error.code -> whether the connection stays open is up to the server; value = retryable. */
export const ERROR_CODES: Record<string, boolean> = {
  "bad_request": false,
  "executor_unavailable": true,
  "internal": true,
  "llm_auth": false,
  "llm_error": false,
  "llm_rate_limit": true,
  "llm_timeout": true,
  "llm_unavailable": true,
  "memory_unavailable": true,
  "not_found": false,
  "session_not_found": false,
  "turn_in_progress": true,
  "unauthorized": false,
  "unsupported_version": false,
  "voice_busy": false,
  "voice_unavailable": false
};
