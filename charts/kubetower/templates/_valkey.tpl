{{/*
Several replicas, and the Valkey they share.

Everything about `replicaCount` above one lives in this file, valkey.yaml and
replicas-refused.yaml, so that the console's own templates carry one include
each rather than the whole mechanism.
*/}}

{{/* Whether this release runs more than one console. */}}
{{- define "kubetower.several" -}}
{{- if gt (int .Values.replicaCount) 1 }}true{{ end }}
{{- end }}

{{- define "kubetower.valkeyName" -}}
{{- printf "%s-valkey" (include "kubetower.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "kubetower.valkeySelectorLabels" -}}
app.kubernetes.io/name: {{ include "kubetower.name" . }}-valkey
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* The Secret holding the Valkey password: the operator's, or this chart's. */}}
{{- define "kubetower.valkeySecretName" -}}
{{- if .Values.valkey.existingSecret }}
{{- .Values.valkey.existingSecret }}
{{- else }}
{{- include "kubetower.valkeyName" . }}
{{- end }}
{{- end }}

{{- define "kubetower.valkeySecretKey" -}}
{{- if .Values.valkey.existingSecret }}
{{- .Values.valkey.existingSecretKey }}
{{- else -}}
password
{{- end }}
{{- end }}

{{/*
The password, drawn once and kept.

What is already in the cluster, else a new one. **A password drawn on every
render changes on every `helm upgrade`**, which restarts Valkey with a new one,
empties it, and signs everybody out - so the Secret the last install wrote is
read back first.

**The limit of `lookup`, stated rather than discovered**: it answers only when
Helm talks to a cluster. `helm template`, `helm install --dry-run` and every
GitOps tool that renders without one - Argo CD among them - get nothing back,
so each of their renders draws a new password. Under those, set
`valkey.existingSecret` to a Secret you manage.
*/}}
{{- define "kubetower.valkeyPassword" -}}
{{- $existing := lookup "v1" "Secret" .Release.Namespace (include "kubetower.valkeyName" .) }}
{{- if and $existing $existing.data (index $existing.data "password") }}
{{- index $existing.data "password" | b64dec }}
{{- else }}
{{- randAlphaNum 48 }}
{{- end }}
{{- end }}

{{/*
The console's half: where the store is and how to authenticate to it, and the
statement that this process is one of several. Included in the Deployment's
env block; renders nothing with one replica.
*/}}
{{- define "kubetower.valkeyEnv" -}}
{{- if include "kubetower.several" . }}
# One of several replicas. The console refuses to start without the store,
# and does not offer what only one pod can hold - preference writes, settings
# writes, port-forwards, the archive.
- name: KT_REPLICATED
  value: "true"
- name: KT_VALKEY_ADDR
  value: {{ printf "%s:%d" (include "kubetower.valkeyName" .) (int .Values.valkey.service.port) | quote }}
- name: KT_VALKEY_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "kubetower.valkeySecretName" . | quote }}
      key: {{ include "kubetower.valkeySecretKey" . | quote }}
{{- end }}
{{- end }}
