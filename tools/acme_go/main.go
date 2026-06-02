package main

import (
	"fmt"
	"os"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: acme-go <ping|health>")
		os.Exit(2)
	}

	switch os.Args[1] {
	case "ping":
		fmt.Println("go-ok")
	case "health":
		fmt.Println(`{"runtime":"go","ok":true}`)
	default:
		fmt.Fprintln(os.Stderr, "usage: acme-go <ping|health>")
		os.Exit(2)
	}
}
