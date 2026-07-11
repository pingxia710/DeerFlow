#!/bin/sh

set -eu

output_path="${1:-/etc/nginx/conf.d/deer-flow-exposure.conf}"
tmp_path="$(mktemp "${output_path}.tmp.XXXXXX")"
trap 'rm -f "$tmp_path"' EXIT HUP INT TERM

if [ "${DEER_FLOW_EXPOSE_API_DOCS:-false}" = "true" ]; then
    printf '%s\n' 'set $expose_api_docs 1;' >"$tmp_path"
else
    printf '%s\n' 'set $expose_api_docs 0;' >"$tmp_path"
fi

trusted_outer_proxies="${DEER_FLOW_TRUSTED_OUTER_PROXIES:-}"
if [ -n "$trusted_outer_proxies" ]; then
    old_ifs=$IFS
    IFS=,
    set -f
    # Intentional field splitting: the environment variable is a comma-separated
    # list. Globbing is disabled and every resulting value is validated below.
    # shellcheck disable=SC2086
    set -- $trusted_outer_proxies
    set +f
    IFS=$old_ifs

    for trusted_proxy in "$@"; do
        trusted_proxy="$(printf '%s' "$trusted_proxy" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        address="${trusted_proxy%/*}"
        prefix_length="${trusted_proxy##*/}"
        case "$trusted_proxy" in
            '' | *[!0-9A-Fa-f.:/]* | */*/* )
                printf 'invalid trusted outer proxy CIDR: %s\n' "$trusted_proxy" >&2
                exit 1
                ;;
        esac
        case "$prefix_length" in
            '' | *[!0-9]* )
                printf 'invalid trusted outer proxy CIDR: %s\n' "$trusted_proxy" >&2
                exit 1
                ;;
        esac
        case "$address" in
            *:*) invalid_address="$(printf '%s' "$address" | tr -d '0-9A-Fa-f:.' )" ;;
            *.*) invalid_address="$(printf '%s' "$address" | tr -d '0-9.' )" ;;
            *) invalid_address=invalid ;;
        esac
        if [ -n "$invalid_address" ]; then
            printf 'invalid trusted outer proxy CIDR: %s\n' "$trusted_proxy" >&2
            exit 1
        fi
        printf 'set_real_ip_from %s;\n' "$trusted_proxy" >>"$tmp_path"
    done

    printf '%s\n' \
        'real_ip_header X-Forwarded-For;' \
        'real_ip_recursive on;' >>"$tmp_path"
fi

mv -f "$tmp_path" "$output_path"
trap - EXIT HUP INT TERM
