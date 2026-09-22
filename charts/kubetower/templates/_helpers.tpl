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
already in the cluster, else a new one. `lookup` returns nothing under
`helm template` and during a dry run, so the rendered manifest there carries a
key that is never installed - which is correct but worth knowing before
diffing two renders and finding them different.
*/}}
{{- define "kubetower.sessionSecret" -}}
{{- if .Values.auth.sessionSecret }}
{{- .Values.auth.sessionSecret }}
{{- else }}
{{- $existing := lookup "v1" "Secret" .Release.Namespace (include "kubetower.fullname" .) }}
{{- if and $existing $existing.data (index $existing.data "KT_SESSION_SECRET") }}
{{- index $existing.data "KT_SESSION_SECRET" | b64dec }}
{{- else }}
{{- randAlphaNum 64 }}
{{- end }}
{{- end }}
{{- end }}

{{- define "kubetower.image" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) }}
{{- end }}
