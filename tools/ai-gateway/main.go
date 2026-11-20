// This file is part of runrist. GPL-3.0-or-later; see COPYING.
// Forward the inference routes of a loopback server to one private VPN peer.
package main

import (
	"compress/gzip"
	"crypto/subtle"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/netip"
	"net/url"
	"os"
	"strings"
)

func main() {
	listen := flag.String("listen", "", "VPN address:port to listen on")
	peer := flag.String("peer", "", "allowed VPN client IP")
	upstream := flag.String("upstream", "", "inference server origin")
	tokenFile := flag.String("token-file", "", "file containing the bearer token")
	flag.Parse()

	addr, err := netip.ParseAddrPort(*listen)
	if err != nil || addr.Addr().IsUnspecified() {
		log.Fatal("specify an individual VPN listen address")
	}
	client, err := netip.ParseAddr(*peer)
	if err != nil {
		log.Fatal("specify a VPN peer IP")
	}
	target, err := url.Parse(*upstream)
	if err != nil || target.Host == "" || (target.Scheme != "http" && target.Scheme != "https") {
		log.Fatal("specify an HTTP upstream origin")
	}
	secret, err := os.ReadFile(*tokenFile)
	if err != nil {
		log.Fatal(err)
	}
	token := strings.TrimSpace(string(secret))
	if token == "" {
		log.Fatal("empty token file")
	}
	proxy := &httputil.ReverseProxy{Rewrite: func(r *httputil.ProxyRequest) {
		r.SetURL(target)
		r.Out.Header.Del("Authorization")
	}}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		remote, err := netip.ParseAddrPort(r.RemoteAddr)
		if err != nil || remote.Addr().Unmap() != client.Unmap() ||
			subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+token)) != 1 {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		if !(r.Method == "GET" && r.URL.Path == "/v1/models") &&
			!(r.Method == "POST" && r.URL.Path == "/v1/chat/completions") {
			http.NotFound(w, r)
			return
		}
		if r.Header.Get("Content-Encoding") == "gzip" {
			reader, err := gzip.NewReader(r.Body)
			if err != nil {
				http.Error(w, "invalid gzip", http.StatusBadRequest)
				return
			}
			defer reader.Close()
			r.Body = reader
			r.ContentLength = -1
			r.Header.Del("Content-Encoding")
			r.Header.Del("Content-Length")
		}
		proxy.ServeHTTP(w, r)
	})

	socket, err := net.Listen("tcp", addr.String())
	if err != nil {
		log.Fatal(err)
	}
	fmt.Println("Listening on", socket.Addr())
	log.Fatal(http.Serve(socket, handler))
}
