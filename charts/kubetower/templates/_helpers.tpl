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
