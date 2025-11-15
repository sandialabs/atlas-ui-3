{{/*
  Define chart labels
*/}}
{{- define "chart.labels" -}}
{{-   with .Values.labels -}}
{{      toYaml . }}
{{-   end }}
{{- end }}
