# Reference outputs for the methods of compare.columns (SPEC §9.5, §13.4; D338).
#
# Run with base R, no packages:  Rscript columns.R > columns.json
# The checked-in columns.json was written by R 4.3.3. Welch's t test and its interval are
# t.test's (var.equal = FALSE), Welch's one-way test oneway.test's (var.equal = FALSE), the rank
# tests wilcox.test's (correct = TRUE; exact when both groups hold fewer than 50 values and none
# are tied) and kruskal.test's, the tails pt's, qt's and pf's, and medians median's. Degrees of
# freedom reach 1e8, and pbeta's parameters 8e6; levels stay at or below 0.999, since qt is
# inexact beyond (1e-7 relative at 1 - 1e-9, where the implementation is within 1e-14).
# as.character, and so kruskal.test's ties, depend on R's long double: this must run on an R whose
# long double is x87's 64-bit mantissa (x86-64; .Machine$sizeof.longdouble 16), which the output
# records and test_stats checks. On arm64 (a double on macOS, a quad on Linux) R prints some values
# otherwise, about 1 in 100 of hard cases without a long double, so the fixture cannot be written
# there (D338).

num <- function(x) if (is.na(x)) "null" else sprintf("%.17g", x)
vec <- function(x) paste0("[", paste(vapply(x, num, ""), collapse = ", "), "]")
obj <- function(...) {
  parts <- list(...)
  paste0("{", paste(sprintf("\"%s\": %s", names(parts), unlist(parts)), collapse = ", "), "}")
}
groups <- function(g) paste0("[", paste(vapply(g, vec, ""), collapse = ", "), "]")

lines <- character()
add <- function(line) lines <<- c(lines, line)

pairs <- list(
  list(c(1.5, 2.25, 3, 4.75, 5, 9), c(0, 2, 2.5, 3.1, 8, 8.5, 12)),
  list(c(10, 12), c(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)),
  list(c(-1e6, 5e5, 3), c(2, 2.5)),
  list(c(0.1, 0.2, 0.30000000000000004), c(0.1, 0.2, 0.3)),
  list(seq(1, 200, by = 1.5), seq(3, 150, by = 0.75)),
  list(c(20, 27, 34, 41, 48, 55, 62, 69), c(22, 29, 36, 43, 50, 57, 64, 71, 78, 85))
)
entries <- character()
for (p in pairs) for (level in c(0.95, 0.9)) {
  t <- t.test(p[[1]], p[[2]], var.equal = FALSE, conf.level = level)
  entries <- c(entries, obj(x = vec(p[[1]]), y = vec(p[[2]]), level = num(level),
                            statistic = num(unname(t$statistic)), df = num(unname(t$parameter)),
                            p = num(t$p.value), low = num(t$conf.int[1]), high = num(t$conf.int[2])))
}
add(sprintf("\"welch\": [%s]", paste(entries, collapse = ",\n  ")))

sets <- list(
  list(c(1.5, 2.25, 3, 4.75, 5, 9), c(0, 2, 2.5, 3.1, 8, 8.5, 12), c(10, 11, 13, 13.5, 20)),
  list(c(1, 2, 3), c(1, 2, 4), c(1, 3, 3), c(8, 9, 10.5)),
  list(seq(0, 100, by = 5), seq(3, 60, by = 3), c(50, 51, 49, 50.5), c(-5, 5, 0, 1, -1, 2))
)
entries <- character()
for (s in sets) {
  y <- unlist(s)
  g <- factor(rep(seq_along(s), vapply(s, length, 0L)))
  o <- oneway.test(y ~ g, data.frame(y = y, g = g), var.equal = FALSE)
  entries <- c(entries, obj(groups = groups(s), statistic = num(unname(o$statistic)),
                            numerator = num(unname(o$parameter[1])),
                            denominator = num(unname(o$parameter[2])), p = num(o$p.value)))
}
add(sprintf("\"welch_anova\": [%s]", paste(entries, collapse = ",\n  ")))

pairs <- list(
  list(c(1.5, 2.25, 3, 4.75, 5, 9), c(0, 2, 2.5, 3.1, 8, 8.5, 12)),
  list(c(1, 2, 3), c(4, 5, 6, 7)),
  list(c(10, 20, 30), c(1, 2, 3)),
  list(c(1, 2, 2, 3), c(2, 3, 4, 4, 5)),
  list(c(5, 5, 5), c(5, 5, 6)),
  list(seq(0.5, 60, by = 1), seq(1, 30, by = 1)),
  list(seq(1, 49), seq(1.5, 49.5)),
  list(c(3.3, 1.1, 2.2), c(2.2, 5.5)),
  list(c(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13), c(14, 15, 16, 17, 18, 19, 20, 21, 22))
)
entries <- character()
for (p in pairs) {
  w <- suppressWarnings(wilcox.test(p[[1]], p[[2]], correct = TRUE))
  entries <- c(entries, obj(x = vec(p[[1]]), y = vec(p[[2]]),
                            statistic = num(unname(w$statistic)), p = num(w$p.value)))
}
add(sprintf("\"mann_whitney\": [%s]", paste(entries, collapse = ",\n  ")))

sets <- list(
  list(c(1.5, 2.25, 3, 4.75, 5, 9), c(0, 2, 2.5, 3.1, 8, 8.5, 12), c(10, 11, 13, 13.5, 20)),
  list(c(1, 2, 2, 3), c(2, 3, 4, 4, 5)),
  list(c(1, 1, 1, 2), c(1, 2, 2, 2), c(2, 2, 3, 3, 3)),
  list(seq(1, 40), seq(20, 70), seq(-10, 10), seq(5, 6)),
  list(c(0.1, 0.2, 0.1 + 0.2), c(0.3, 0.5), c(1, 2)),
  list(c(1234567890123456, 1234567890123457, 1234567890123459),
       c(1234567890123460, 1234567890123470, 1234567890123480)),
  list(c(1e15 + 1, 1e15 + 3, 2e15), c(1e15 + 2, 5e15 + 4, 5e15 + 5), c(9e15, 9e15 + 2)),
  list(c(123456789012345.6, 123456789012345.62, 1), c(123456789012345.64, 2, 3), c(4, 5))
)
entries <- character()
for (s in sets) {
  k <- kruskal.test(s)
  entries <- c(entries, obj(groups = groups(s), statistic = num(unname(k$statistic)),
                            df = num(unname(k$parameter)), p = num(k$p.value)))
}
add(sprintf("\"kruskal_wallis\": [%s]", paste(entries, collapse = ",\n  ")))

tails <- list(c(3.2, 1e3), c(0.3, 2.5), c(1.96, 1), c(12, 3.7), c(0, 8), c(40, 50),
              c(2.5, 3e5), c(0.001, 17.25), c(7, 1.5), c(2.165040633821582, 69574152.98469777),
              c(1.5, 5e6), c(4, 1e8), c(22.6, 1.5e7))
entries <- character()
for (t in tails) {
  entries <- c(entries, obj(t = num(t[1]), df = num(t[2]), p = num(2 * pt(-abs(t[1]), t[2]))))
}
add(sprintf("\"t_tail\": [%s]", paste(entries, collapse = ",\n  ")))

quantiles <- list(c(0.95, 3.7), c(0.99, 1e5), c(0.9, 1), c(0.5, 2.25), c(0.999, 4),
                  c(0.95, 10.141004551782313), c(0.8, 250), c(0.95, 5e6), c(0.99, 7e7))
entries <- character()
for (q in quantiles) {
  entries <- c(entries, obj(level = num(q[1]), df = num(q[2]), t = num(qt((1 + q[1]) / 2, q[2]))))
}
add(sprintf("\"t_quantile\": [%s]", paste(entries, collapse = ",\n  ")))

tails <- list(c(3.3, 2, 11.4), c(0.5, 1, 1), c(9.7, 2, 9.15), c(100, 5, 20), c(1, 30, 3000),
              c(0.01, 3, 7.5), c(250, 1, 2), c(1.7328432762662314, 3, 63779390.93501643),
              c(2.2, 5, 1e7), c(0.8, 2, 4e6))
entries <- character()
for (f in tails) {
  entries <- c(entries, obj(f = num(f[1]), numerator = num(f[2]), denominator = num(f[3]),
                            p = num(pf(f[1], f[2], f[3], lower.tail = FALSE))))
}
add(sprintf("\"f_tail\": [%s]", paste(entries, collapse = ",\n  ")))

betas <- list(c(2e5, 2e5, 0.4995), c(3e4, 4e4, 0.43), c(5e4, 12, 0.9998), c(0.5, 2.5, 0.2),
              c(150, 180, 0.46), c(1e5, 1e5, 0.501),
              c(8389965.275354996, 0.532341720289899, 0.9999997442961841), c(20, 20, 1e-10),
              c(15, 1000, 1e-8), c(12, 12, 1e-300),
              c(3603223.415191399, 39.2052615957463, 0.9999887069612107),
              c(120, 7.3, 0.9), c(2e4, 2.5, 0.9995), c(40, 0.3, 0.95), c(500, 12.7, 0.97),
              c(15, 0.7, 0.75), c(3000, 33.3, 0.985), c(15.43, 0.182, 0.707284),
              c(15.394, 0.103, 0.740048), c(15.271, 0.188, 0.71052))
entries <- character()
for (b in betas) {
  entries <- c(entries, obj(a = num(b[1]), b = num(b[2]), x = num(b[3]),
                            p = num(pbeta(b[3], b[1], b[2]))))
}
add(sprintf("\"beta\": [%s]", paste(entries, collapse = ",\n  ")))

# Doubles as as.character writes them (R 4.3: formatReal at 15 digits, fixed notation whenever it
# is no wider than scientific), across 1e14 to 1e17 and beyond, negative and not integers.
set.seed(46)
printed <- c(1234567890123456 + 0:4, 1234567890123456.5, -1234567890123456.25, 1.7e15 + 0:3,
             9007199254740991, -9007199254740991, 2^53, 999999999999999.7, 99999.99999999999,
             0.1 + 0.2, 0.3, 1/3, 1e5, 1e5 + 0.5, 1e14 + 0.5, 123456789012345.67, 1e16, 1e17,
             123456789012345678, 1e15 - 0.5, 1e15 + 0.5, 10000.00048828125, 1.000000000000005e-09,
             5e-324, -2.5, 0, 1e-20, 1e300, 12345678901234.5, 123456789012.3456,
             floor(runif(60, 1e14, 1e17)), -floor(runif(20, 1e14, 1e16)),
             runif(40, 1e14, 1e16) + runif(40), signif(runif(40), 15) * 10^sample(-20:20, 40,
             replace = TRUE),
             # where R's long double scaling rounds otherwise than printf, and trailing zeros
             as.numeric(c(c("0x1.317b73a93fe4bp-18", "0x1.98db4971b9c9ap+75", "0x1.86050407b261dp+32",
                          "0x1.06da98dd7cfe5p+118", "0x1.5486306760296p-1", "0x1.8ee84d48923abp+28",
                          "0x1.342deb715ccb0p-742", "-0x1.10cb02f158011p-35", "0x1.169a5246af347p-39",
                          "0x1.ffcde7146fcd8p+135"),
                        # where 64 bits of long double round otherwise than 53 or 63
                        c("0x1.bcae77ebfc7edp+633", "0x1.60a1dd62c491cp-172",
                          "0x1.36138012390b7p-539", "-0x1.097f12618fc5ep-904",
                          "0x1.33736f217a5ffp+905", "0x1.38d2663f2ae58p+850",
                          "-0x1.8f386ffffff9ap+22", "0x1.1faec1c95a4e8p-493",
                          "-0x1.65ed1c9aa2ebep+99", "-0x1.d356c16e8711cp+702",
                          "0x1.1ddad89ac418dp+796", "0x1.2c5b448c2c7a9p+474",
                          "-0x1.c0f5be3be1e5bp-795", "0x1.f74538dc243e4p+25"),
                        # just below a power of ten, where the scaled value is below 1e14
                        c("0x0.012688b70e62bp-1022", "0x1.192e9ee706e47p-525",
                          "-0x1.0c6f7a0b5ed8ap-20", "0x1.23576845996fep+528",
                          "-0x1.6909f3b92c831p+923", "0x1.9f623d5a8a72cp-107",
                          "0x1.431e0fae6d70bp+96", "0x1.e17b843576917p+122"))))
entries <- vapply(printed, function(x) obj(x = num(x), text = sprintf("\"%s\"", as.character(x))), "")
add(sprintf("\"printed\": [%s]", paste(entries, collapse = ",\n  ")))

cat(sprintf("{\"r\": \"%s\", \"platform\": \"%s\", \"long_double\": %d,\n%s}\n", R.version.string,
            R.version$platform, .Machine$sizeof.longdouble, paste(lines, collapse = ",\n")))
