{{/* Name helpers, the ordinary ones. */}}
{{- define "kubetower.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "kubetower.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "kubetower.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "kubetower.labels" -}}
helm.sh/chart: {{ include "kubetower.chart" . }}
{{ include "kubetower.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "kubetower.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kubetower.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "kubetower.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "kubetower.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
The Secret holding KT_PASSWORD_HASH and KT_SESSION_SECRET: either one the
operator made, or the one this chart makes.
*/}}
{{- define "kubetower.secretName" -}}
{{- if .Values.auth.existingSecret }}
{{- .Values.auth.existingSecret }}
{{- else }}
{{- include "kubetower.fullname" . }}
{{- end }}
{{- end }}

{{/*
The session key, preserved across upgrades.

A freshly drawn key on every `helm upgrade` signs everybody out, which looks
like a bug rather than a rotation. So: what the values say, else what is
already in the cluster, else a new one.

`lookup` returns nothing under `helm template`, during a dry run, and in a
GitOps tool that renders without a cluster - Argo CD among them. There, every
render draws a new key. Installed that way, the Secret changes on every sync,
and the checksum on the Deployment then rolls the pod each time, signing
everybody out; without the checksum, pods started at different times would
hold different keys and refuse each other's sessions. Set auth.sessionSecret
or auth.existingSecret for those tools.

The value is drawn once per render and remembered for the rest of it, so the
Secret and the Deployment's checksum over it describe the same key. Without
that, the checksum would hash a second, different draw.

The memo lives in the values, which every template of the render shares - and
which a user can write to as well, skipping every check above. So
values.schema.json refuses auth._drawnSessionSecret before rendering starts.
*/}}
{{- define "kubetower.sessionSecret" -}}
{{- if not (hasKey .Values.auth "_drawnSessionSecret") }}
{{- $value := "" }}
{{- if .Values.auth.sessionSecret }}
{{- if lt (len .Values.auth.sessionSecret) 32 }}
{{- fail "auth.sessionSecret is shorter than 32 characters: the console would ignore it and draw its own key at every start, signing everybody out on each restart. Use at least 32 characters, e.g. the output of `openssl rand -hex 32`." }}
{{- end }}
{{- $value = .Values.auth.sessionSecret }}
{{- else }}
{{- $existing := lookup "v1" "Secret" .Release.Namespace (include "kubetower.fullname" .) }}
{{- if and $existing $existing.data (index $existing.data "KT_SESSION_SECRET") }}
{{- $value = index $existing.data "KT_SESSION_SECRET" | b64dec }}
{{- else }}
{{- $value = randAlphaNum 64 }}
{{- end }}
{{- end }}
{{- $_ := set .Values.auth "_drawnSessionSecret" $value }}
{{- end }}
{{- index .Values.auth "_drawnSessionSecret" }}
{{- end }}

{{- define "kubetower.image" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) }}
{{- end }}

{{/*
Whether this release renders a kubeconfig: only for the desktop profile, and
only when kubeconfig.generate asks for one. Under the server profile the
console reads the pod's own ServiceAccount and never reads KUBECONFIG, so a
kubeconfig there would be a ConfigMap and a variable nothing reads.
*/}}
{{- define "kubetower.kubeconfigGenerated" -}}
{{- if and .Values.kubeconfig.generate (not .Values.serverProfile) }}true{{ end }}
{{- end }}

{{/*
The pod's grace period: what the values say, refused when it cannot hold the
preStop pause plus the console's own shutdown. On SIGTERM the console gives
in-flight requests five seconds (internal/launch/run.go `shutdown` in
thonicdev/kubetower) and then exits; a grace period shorter than the pause
and those five seconds kills it in the middle of either.
*/}}
{{- define "kubetower.terminationGracePeriod" -}}
{{- $grace := int .Values.terminationGracePeriodSeconds }}
{{- $pause := int .Values.preStopSleepSeconds }}
{{- $shutdown := 5 }}
{{- if lt $grace (add $pause $shutdown) }}
{{- fail (printf "terminationGracePeriodSeconds is %d, shorter than preStopSleepSeconds (%d) plus the console's own %d-second shutdown: the pod would be killed before it finished. Set it to at least %d." $grace $pause $shutdown (add $pause $shutdown)) }}
{{- end }}
{{- $grace }}
{{- end }}

{{/*
Where several consoles are scheduled: spread across nodes, so that one drain
does not take every replica at once. ScheduleAnyway, so a cluster of one node
still schedules them all rather than leaving some Pending.

Only with several replicas, and only when the values do not give their own
topologySpreadConstraints, which then replace this one entirely.
*/}}
{{- define "kubetower.topologySpread" -}}
{{- if .Values.topologySpreadConstraints }}
topologySpreadConstraints:
  {{- toYaml .Values.topologySpreadConstraints | nindent 2 }}
{{- else if include "kubetower.several" . }}
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        {{- include "kubetower.selectorLabels" . | nindent 8 }}
{{- end }}
{{- end }}

{{/*
GOMEMLIMIT, in bytes: goMemLimitPercent of resources.limits.memory.

Empty - and the variable then absent - when there is no memory limit, when
goMemLimitPercent is 0, or when extraEnv sets GOMEMLIMIT itself (two entries
of one name in a container's env is a patch Helm cannot apply cleanly).

The quantity is read here rather than through a resourceFieldRef, because the
runtime wants a share of the limit and not the limit itself: the heap is not
all of the process's memory, and a soft limit equal to the hard one leaves no
room for the rest. Kubernetes' integer forms are accepted (a number, or one
followed by k, M, G, T, Ki, Mi, Gi or Ti); anything else is refused with what
to write instead, rather than silently leaving the runtime unpaced.
*/}}
{{- define "kubetower.goMemLimit" -}}
{{- $own := false }}
{{- range .Values.extraEnv }}{{ if eq (toString .name) "GOMEMLIMIT" }}{{ $own = true }}{{ end }}{{ end }}
{{- $limit := "" }}
{{- with .Values.resources }}{{ with .limits }}{{ with .memory }}
{{- /* An unquoted byte count in a values file reaches Helm as a float64,
       which toString writes as 1.073741824e+09. A whole number is the
       integer the user wrote; anything else is left as it is and refused
       below. */}}
{{- if and (kindIs "float64" .) (eq (. | int64 | float64) .) }}
{{- $limit = . | int64 | toString }}
{{- else }}
{{- $limit = toString . }}
{{- end }}
{{- end }}{{ end }}{{ end }}
{{- $percent := int .Values.goMemLimitPercent }}
{{- if and $limit (gt $percent 0) (not $own) }}
{{- if or (gt $percent 100) (not (regexMatch "^[0-9]+(k|M|G|T|Ki|Mi|Gi|Ti)?$" $limit)) }}
{{- if gt $percent 100 }}
{{- fail (printf "goMemLimitPercent is %d: GOMEMLIMIT above the container's memory limit paces nothing. Use 1 to 100, or 0 to leave GOMEMLIMIT unset." $percent) }}
{{- end }}
{{- fail (printf "resources.limits.memory is %q, which the chart cannot turn into GOMEMLIMIT. Write it as an integer, optionally followed by k, M, G, T, Ki, Mi, Gi or Ti (1Gi, 1500M), or set goMemLimitPercent to 0, or set GOMEMLIMIT yourself through extraEnv." $limit) }}
{{- end }}
{{- $units := dict "" 1 "k" 1000 "M" 1000000 "G" 1000000000 "T" 1000000000000 "Ki" 1024 "Mi" 1048576 "Gi" 1073741824 "Ti" 1099511627776 }}
{{- $number := regexFind "^[0-9]+" $limit | int64 }}
{{- $unit := regexReplaceAll "^[0-9]+" $limit "" }}
{{- div (mul $number (index $units $unit) $percent) 100 }}
{{- end }}
{{- end }}

{{/*
Whether a switch in console-needs.yaml is on: "true" or "false".

`always` is always on. Any other switch is `rbac.<key>`, read from the rbac
values, and a key that does not exist there is refused rather than read as
off - a misspelt switch would otherwise drop its grants without a word.
`rbac.readSecrets` applies only with `rbac.read`, as values.yaml says.
*/}}
{{- define "kubetower.rbacSwitch" -}}
{{- $switch := .switch -}}
{{- if eq $switch "always" -}}
true
{{- else -}}
{{- $key := trimPrefix "rbac." $switch -}}
{{- if or (eq $key $switch) (not (hasKey .rbac $key)) -}}
{{- fail (printf "console-needs.yaml names the switch %q, which is not a value under rbac" $switch) -}}
{{- end -}}
{{- if eq $key "readSecrets" -}}
{{- and .rbac.read .rbac.readSecrets | toString -}}
{{- else -}}
{{- index .rbac $key | toString -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
auth.trustedProxies as KT_TRUSTED_PROXIES: the entries joined by commas, or
nothing when there are none. A list, or one string. Every entry, a list's
included, is split on commas and any whitespace - spaces, tabs and the
newlines of a block scalar with one entry per line - because the console
splits on commas and spaces only and refuses the rest. Empty pieces are
dropped, so `--set auth.trustedProxies=""` means none rather than an entry
the console would refuse.

Empty as well when extraEnv sets KT_TRUSTED_PROXIES itself: extraEnv wins, as
it does for GOMEMLIMIT, since two entries of one name in a container's env is
a patch Helm cannot apply cleanly.
*/}}
{{- define "kubetower.trustedProxies" -}}
{{- if not (include "kubetower.trustedProxiesInExtraEnv" .) -}}
{{- include "kubetower.trustedProxiesParsed" . -}}
{{- end -}}
{{- end -}}

{{/* auth.trustedProxies parsed as above, whatever extraEnv says. */}}
{{- define "kubetower.trustedProxiesParsed" -}}
{{- $v := .Values.auth.trustedProxies -}}
{{- $raw := list -}}
{{- if kindIs "string" $v -}}
{{- $raw = list $v -}}
{{- else if $v -}}
{{- $raw = $v -}}
{{- end -}}
{{- $out := list -}}
{{- range $raw -}}
{{- range regexSplit "[\\s,]+" (toString .) -1 -}}
{{- with . -}}
{{- $out = append $out . -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- join "," $out -}}
{{- end -}}

{{/* "true" when extraEnv names KT_TRUSTED_PROXIES itself, otherwise empty. */}}
{{- define "kubetower.trustedProxiesInExtraEnv" -}}
{{- range .Values.extraEnv }}{{ if eq (toString .name) "KT_TRUSTED_PROXIES" }}true{{ end }}{{ end -}}
{{- end -}}

{{/*
extras as KT_EXTRAS: the keys joined by commas, each once, in the order
given - the list the console reads, where a key alone means on. Empty when
extras names none, and then neither the ConfigMap nor the variable is
rendered, so an install that names no extra renders what it did before.

Empty as well when extraEnv sets KT_EXTRAS itself: extraEnv wins, as it does
for GOMEMLIMIT and KT_TRUSTED_PROXIES, since two entries of one name in a
container's env is a patch Helm cannot apply cleanly. values.schema.json has
already refused anything but a list of lower-case keys.
*/}}
{{- define "kubetower.extras" -}}
{{- if not (include "kubetower.extrasInExtraEnv" .) -}}
{{- .Values.extras | default list | uniq | join "," -}}
{{- end -}}
{{- end -}}

{{/* "true" when extraEnv names KT_EXTRAS itself, otherwise empty. */}}
{{- define "kubetower.extrasInExtraEnv" -}}
{{- range .Values.extraEnv }}{{ if eq (toString .name) "KT_EXTRAS" }}true{{ end }}{{ end -}}
{{- end -}}

{{/*
extraSettings as KT_EXTRA_SETTINGS: `<extra>.<setting>=on|off`, joined by
commas, in key order (a map has no other) - the console's grammar. Empty when
extraSettings names none, and then the variable is not rendered, so an
install that names no setting renders what it did before. Empty as well when
extraEnv sets KT_EXTRA_SETTINGS itself, which then wins, as for KT_EXTRAS.
values.schema.json has already refused anything but extra -> setting ->
boolean, with lower-case keys.
*/}}
{{- define "kubetower.extraSettings" -}}
{{- if not (include "kubetower.extraSettingsInExtraEnv" .) -}}
{{- $out := list -}}
{{- range $extra, $settings := .Values.extraSettings | default dict -}}
{{- range $name, $on := $settings -}}
{{- $out = append $out (printf "%s.%s=%s" $extra $name (ternary "on" "off" $on)) -}}
{{- end -}}
{{- end -}}
{{- join "," $out -}}
{{- end -}}
{{- end -}}

{{/* "true" when extraEnv names KT_EXTRA_SETTINGS itself, otherwise empty. */}}
{{- define "kubetower.extraSettingsInExtraEnv" -}}
{{- range .Values.extraEnv }}{{ if eq (toString .name) "KT_EXTRA_SETTINGS" }}true{{ end }}{{ end -}}
{{- end -}}
