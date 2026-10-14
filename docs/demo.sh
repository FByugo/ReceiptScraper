cd "$(dirname "$0")/.."; source docs/_say.sh; export PATH="$PWD/.venv/bin:$PATH"; clear
note "six pages, including one that blocks readers and one robots.txt forbids"
run "grep -v '^#' examples/urls.txt"
run "python -m receipt_scraper run examples/urls.txt --out /tmp/rs --delay 0.5"
run "cut -d, -f1-3,15 /tmp/rs/results.csv | column -s, -t"
note "every saved page has a fingerprint. change one byte and verify catches it"
run "python -m receipt_scraper verify /tmp/rs"
run "f=\$(ls /tmp/rs/receipts/*.raw | head -1); printf x >> \$f; python -m receipt_scraper verify /tmp/rs; echo exit=\$?"
