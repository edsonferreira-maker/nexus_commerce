import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:file_picker/file_picker.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

import 'product_browser_capture.dart';
import 'traffic_map.dart';

const ink = Color(0xFF08111F);
const panel = Color(0xFF101B2D);
const soft = Color(0xFF162338);
const line = Color(0xFF25334A);
const muted = Color(0xFF8D9AAF);
const green = Color(0xFF72E59B);
const purple = Color(0xFF8B7CFF);
const orange = Color(0xFFFFB86A);
const red = Color(0xFFFF7885);
const selfHosted = bool.fromEnvironment('NEXO_SELF_HOSTED');

class NexoApp extends StatelessWidget {
  const NexoApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'Nexo Commerce',
    debugShowCheckedModeBanner: false,
    theme: ThemeData(
      brightness: Brightness.dark,
      scaffoldBackgroundColor: ink,
      colorScheme: const ColorScheme.dark(
        primary: green,
        secondary: purple,
        surface: panel,
        error: red,
      ),
      textTheme: const TextTheme(
        displaySmall: TextStyle(
          fontSize: 34,
          fontWeight: FontWeight.w800,
          letterSpacing: -1.2,
        ),
        headlineSmall: TextStyle(fontSize: 24, fontWeight: FontWeight.w800),
        titleLarge: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
        bodyMedium: TextStyle(fontSize: 14, height: 1.45),
      ),
      dividerColor: line,
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: ink.withValues(alpha: .7),
        hintStyle: const TextStyle(color: muted),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 16,
          vertical: 15,
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: line),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: line),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: green),
        ),
      ),
      cardTheme: CardThemeData(
        color: panel,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(18),
          side: const BorderSide(color: line),
        ),
      ),
      snackBarTheme: const SnackBarThemeData(
        backgroundColor: soft,
        behavior: SnackBarBehavior.floating,
      ),
      useMaterial3: true,
    ),
    onGenerateRoute: (settings) {
      final path = settings.name ?? '/';
      if (path.startsWith('/p/') && path.split('/').length == 3) {
        return MaterialPageRoute<void>(
          settings: settings,
          builder: (_) => PublicProductPage(slug: path.substring(3)),
        );
      }
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) => selfHosted ? const AuthGate() : const CommerceShell(),
      );
    },
  );
}

class AuthGate extends StatefulWidget {
  const AuthGate({super.key});

  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate> {
  bool? authenticated;

  @override
  void initState() {
    super.initState();
    checkSession();
  }

  Future<void> checkSession() async {
    try {
      final response = await http.get(Uri.base.resolve('/api/auth/session'));
      if (mounted) setState(() => authenticated = response.statusCode == 200);
    } catch (_) {
      if (mounted) setState(() => authenticated = false);
    }
  }

  Future<void> logout() async {
    try {
      await http.post(
        Uri.base.resolve('/api/auth/logout'),
        headers: const {'Content-Type': 'application/json'},
        body: '{}',
      );
    } finally {
      if (mounted) setState(() => authenticated = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (authenticated == null) return const LoginLoadingScreen();
    if (authenticated == false) {
      return LoginScreen(
        onAuthenticated: () => setState(() => authenticated = true),
      );
    }
    return CommerceShell(onLogout: logout);
  }
}

class LoginLoadingScreen extends StatelessWidget {
  const LoginLoadingScreen({super.key});

  @override
  Widget build(BuildContext context) => const Scaffold(
    body: Center(
      child: SizedBox(
        width: 28,
        height: 28,
        child: CircularProgressIndicator(strokeWidth: 2.5),
      ),
    ),
  );
}

class LoginScreen extends StatefulWidget {
  const LoginScreen({required this.onAuthenticated, super.key});

  final VoidCallback onAuthenticated;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final username = TextEditingController();
  final password = TextEditingController();
  final formKey = GlobalKey<FormState>();
  bool obscurePassword = true;
  bool submitting = false;
  String? error;

  @override
  void dispose() {
    username.dispose();
    password.dispose();
    super.dispose();
  }

  Future<void> submit() async {
    if (submitting || !(formKey.currentState?.validate() ?? false)) return;
    setState(() {
      submitting = true;
      error = null;
    });
    try {
      final response = await http.post(
        Uri.base.resolve('/api/auth/login'),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({
          'username': username.text.trim(),
          'password': password.text,
        }),
      );
      if (response.statusCode == 200) {
        TextInput.finishAutofillContext(shouldSave: true);
        widget.onAuthenticated();
        return;
      }
      var message = 'Usuário ou senha incorretos.';
      try {
        message =
            (jsonDecode(response.body) as Map<String, dynamic>)['message']
                as String? ??
            message;
      } catch (_) {}
      if (mounted) setState(() => error = message);
    } catch (_) {
      if (mounted) {
        setState(
          () => error = 'Não foi possível acessar o servidor. Tente novamente.',
        );
      }
    } finally {
      if (mounted) setState(() => submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.sizeOf(context);
    final desktop = size.width >= 900;
    return Scaffold(
      body: Stack(
        children: [
          const Positioned.fill(child: _LoginBackdrop()),
          SafeArea(
            child: Row(
              children: [
                if (desktop)
                  const Expanded(flex: 11, child: _LoginBrandPanel()),
                Expanded(
                  flex: desktop ? 9 : 1,
                  child: Center(
                    child: SingleChildScrollView(
                      padding: EdgeInsets.symmetric(
                        horizontal: desktop ? 64 : 22,
                        vertical: 32,
                      ),
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 430),
                        child: _loginCard(desktop),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _loginCard(bool desktop) => Container(
    padding: EdgeInsets.all(desktop ? 38 : 26),
    decoration: BoxDecoration(
      color: const Color(0xF2121E31),
      borderRadius: BorderRadius.circular(28),
      border: Border.all(color: Colors.white.withValues(alpha: .09)),
      boxShadow: const [
        BoxShadow(
          color: Color(0x66000000),
          blurRadius: 60,
          offset: Offset(0, 24),
        ),
      ],
    ),
    child: Form(
      key: formKey,
      child: AutofillGroup(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (!desktop) ...[
              const _CompactLoginBrand(),
              const SizedBox(height: 32),
            ],
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
              decoration: BoxDecoration(
                color: green.withValues(alpha: .1),
                borderRadius: BorderRadius.circular(99),
                border: Border.all(color: green.withValues(alpha: .22)),
              ),
              child: const Text(
                'PAINEL ADMINISTRATIVO',
                style: TextStyle(
                  color: green,
                  fontSize: 10,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 1.2,
                ),
              ),
            ),
            const SizedBox(height: 20),
            const Text(
              'Bem-vindo de volta',
              style: TextStyle(
                fontSize: 30,
                fontWeight: FontWeight.w800,
                letterSpacing: -1,
              ),
            ),
            const SizedBox(height: 9),
            const Text(
              'Entre para acompanhar sua operação e gerenciar seus produtos.',
              style: TextStyle(color: muted, height: 1.5),
            ),
            const SizedBox(height: 30),
            const Text(
              'Usuário',
              style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 9),
            TextFormField(
              controller: username,
              autofillHints: const [AutofillHints.username],
              autocorrect: false,
              enableSuggestions: false,
              textInputAction: TextInputAction.next,
              decoration: const InputDecoration(
                hintText: 'Digite seu usuário',
                prefixIcon: Icon(
                  Icons.person_outline_rounded,
                  color: muted,
                  size: 20,
                ),
              ),
              validator: (value) => (value?.trim().isEmpty ?? true)
                  ? 'Informe seu usuário.'
                  : null,
            ),
            const SizedBox(height: 20),
            const Text(
              'Senha',
              style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 9),
            TextFormField(
              controller: password,
              autofillHints: const [AutofillHints.password],
              autocorrect: false,
              enableSuggestions: false,
              obscureText: obscurePassword,
              onFieldSubmitted: (_) => submit(),
              decoration: InputDecoration(
                hintText: 'Digite sua senha',
                prefixIcon: const Icon(
                  Icons.lock_outline_rounded,
                  color: muted,
                  size: 20,
                ),
                suffixIcon: IconButton(
                  tooltip: obscurePassword ? 'Mostrar senha' : 'Ocultar senha',
                  onPressed: () =>
                      setState(() => obscurePassword = !obscurePassword),
                  icon: Icon(
                    obscurePassword
                        ? Icons.visibility_outlined
                        : Icons.visibility_off_outlined,
                    color: muted,
                    size: 20,
                  ),
                ),
              ),
              validator: (value) =>
                  (value?.isEmpty ?? true) ? 'Informe sua senha.' : null,
            ),
            if (error != null) ...[
              const SizedBox(height: 16),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(13),
                decoration: BoxDecoration(
                  color: red.withValues(alpha: .1),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: red.withValues(alpha: .28)),
                ),
                child: Row(
                  children: [
                    const Icon(
                      Icons.error_outline_rounded,
                      color: red,
                      size: 19,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        error!,
                        style: const TextStyle(color: red, fontSize: 13),
                      ),
                    ),
                  ],
                ),
              ),
            ],
            const SizedBox(height: 26),
            SizedBox(
              width: double.infinity,
              height: 52,
              child: FilledButton(
                onPressed: submitting ? null : submit,
                style: FilledButton.styleFrom(
                  backgroundColor: green,
                  foregroundColor: ink,
                  disabledBackgroundColor: green.withValues(alpha: .5),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(14),
                  ),
                ),
                child: submitting
                    ? const SizedBox(
                        width: 21,
                        height: 21,
                        child: CircularProgressIndicator(
                          strokeWidth: 2.3,
                          color: ink,
                        ),
                      )
                    : const FittedBox(
                        fit: BoxFit.scaleDown,
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(
                              'Entrar no painel',
                              style: TextStyle(fontWeight: FontWeight.w800),
                            ),
                            SizedBox(width: 9),
                            Icon(Icons.arrow_forward_rounded, size: 19),
                          ],
                        ),
                      ),
              ),
            ),
            const SizedBox(height: 24),
            const Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.shield_outlined, color: muted, size: 15),
                SizedBox(width: 7),
                Flexible(
                  child: Text(
                    'Acesso protegido e seguro',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: muted, fontSize: 12),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    ),
  );
}

class _CompactLoginBrand extends StatelessWidget {
  const _CompactLoginBrand();

  @override
  Widget build(BuildContext context) => const Row(
    children: [
      _BrandMark(size: 48),
      SizedBox(width: 13),
      Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'NEXO',
            style: TextStyle(
              fontSize: 20,
              fontWeight: FontWeight.w900,
              letterSpacing: 1.6,
            ),
          ),
          Text(
            'COMMERCE',
            style: TextStyle(
              color: muted,
              fontSize: 9,
              fontWeight: FontWeight.w700,
              letterSpacing: 2.2,
            ),
          ),
        ],
      ),
    ],
  );
}

class _LoginBrandPanel extends StatelessWidget {
  const _LoginBrandPanel();

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(72, 54, 56, 54),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const _CompactLoginBrand(),
        const Spacer(),
        const _BrandMark(size: 104),
        const SizedBox(height: 34),
        ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 600),
          child: const Text(
            'Sua operação conectada. Seu crescimento em movimento.',
            style: TextStyle(
              fontSize: 46,
              height: 1.08,
              fontWeight: FontWeight.w800,
              letterSpacing: -1.8,
            ),
          ),
        ),
        const SizedBox(height: 20),
        ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 520),
          child: const Text(
            'Produtos, pedidos e performance reunidos em um painel criado para decisões mais rápidas.',
            style: TextStyle(
              color: Color(0xFFADB8C9),
              fontSize: 17,
              height: 1.55,
            ),
          ),
        ),
        const Spacer(),
        const Row(
          children: [
            _LoginFeature(Icons.bolt_rounded, 'Operação ágil'),
            SizedBox(width: 26),
            _LoginFeature(Icons.insights_rounded, 'Dados em tempo real'),
          ],
        ),
      ],
    ),
  );
}

class _BrandMark extends StatelessWidget {
  const _BrandMark({required this.size});
  final double size;

  @override
  Widget build(BuildContext context) => ClipRRect(
    borderRadius: BorderRadius.circular(size * .24),
    child: Image.asset(
      'assets/branding/nexo-commerce-logo.png',
      width: size,
      height: size,
      fit: BoxFit.cover,
    ),
  );
}

class _LoginFeature extends StatelessWidget {
  const _LoginFeature(this.icon, this.label);
  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) => Row(
    children: [
      Container(
        width: 34,
        height: 34,
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: .06),
          borderRadius: BorderRadius.circular(10),
        ),
        child: Icon(icon, color: green, size: 17),
      ),
      const SizedBox(width: 10),
      Text(
        label,
        style: const TextStyle(
          color: Color(0xFFC0C9D6),
          fontSize: 13,
          fontWeight: FontWeight.w600,
        ),
      ),
    ],
  );
}

class _LoginBackdrop extends StatelessWidget {
  const _LoginBackdrop();

  @override
  Widget build(BuildContext context) => DecoratedBox(
    decoration: const BoxDecoration(
      gradient: LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: [Color(0xFF07101E), Color(0xFF0B1525), Color(0xFF10172A)],
      ),
    ),
    child: Stack(
      children: [
        Positioned(
          left: -180,
          top: -210,
          child: Container(
            width: 560,
            height: 560,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: purple.withValues(alpha: .1),
            ),
          ),
        ),
        Positioned(
          right: -140,
          bottom: -220,
          child: Container(
            width: 520,
            height: 520,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: green.withValues(alpha: .07),
            ),
          ),
        ),
      ],
    ),
  );
}

class Product {
  const Product(
    this.name,
    this.market,
    this.price,
    this.image,
    this.sales,
    this.revenue,
    this.mode, {
    this.active = true,
  });
  final String name, market, price, image, revenue, mode;
  final int sales;
  final bool active;
}

const products = <Product>[
  Product(
    'Fone Bluetooth Wave Pro',
    'Mercado Livre',
    'R\$ 289,90',
    'https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=700&q=80',
    146,
    'R\$ 18,2 mil',
    'Checkout próprio',
  ),
  Product(
    'Smartwatch Active S9',
    'Amazon',
    'R\$ 619,00',
    'https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=700&q=80',
    98,
    'R\$ 14,8 mil',
    'Checkout próprio',
  ),
  Product(
    'Tênis Urban Runner',
    'Shopee',
    'R\$ 189,90',
    'https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=700&q=80',
    87,
    'R\$ 9,7 mil',
    'Redirecionamento',
  ),
  Product(
    'Mochila Essentials 24L',
    'Amazon',
    'R\$ 149,80',
    'https://images.unsplash.com/photo-1553062407-98eeb64c6a62?w=700&q=80',
    53,
    'R\$ 7,1 mil',
    'Checkout próprio',
  ),
  Product(
    'Luminária Minimal Desk',
    'Temu',
    'R\$ 89,90',
    'https://images.unsplash.com/photo-1507473885765-e6ed057f782c?w=700&q=80',
    42,
    'R\$ 3,8 mil',
    'Redirecionamento',
    active: false,
  ),
];

class ProductReview {
  const ProductReview(this.author, this.date, this.comment, this.stars);
  final String author, date, comment;
  final int stars;
}

class ProductPageData {
  const ProductPageData({
    this.id = '',
    this.slug = 'fone-wave-pro',
    this.sourceUrl = '',
    this.importNotice = '',
    this.storeName = 'URBAN NEST',
    required this.name,
    required this.price,
    required this.market,
    required this.images,
    required this.description,
    required this.reviews,
    required this.rating,
    required this.reviewCount,
    required this.stock,
    this.redirect = false,
    this.brand = 'WAVE AUDIO',
    this.installmentText = 'ou 6x de R\$ 48,31 sem juros',
    this.shippingText = 'Receba entre 24 e 26 de setembro',
    this.variations = const [],
  });

  final String id, slug, sourceUrl, importNotice, storeName;
  final String name, price, market, description, rating;
  final String brand, installmentText, shippingText;
  final int reviewCount, stock;
  final List<String> images;
  final List<ProductReview> reviews;
  final List<String> variations;
  final bool redirect;

  bool get isImported => id.isNotEmpty;

  factory ProductPageData.fromJson(Map<String, dynamic> json) =>
      ProductPageData(
        id: json['id'] as String? ?? '',
        slug: json['slug'] as String? ?? '',
        sourceUrl: json['sourceUrl'] as String? ?? '',
        importNotice: json['importNotice'] as String? ?? '',
        storeName: json['storeName'] as String? ?? 'Minha loja',
        name: json['name'] as String? ?? '',
        price: json['price'] as String? ?? '',
        market: json['market'] as String? ?? 'Mercado Livre',
        images: (json['images'] as List<dynamic>? ?? []).cast<String>(),
        description: json['description'] as String? ?? '',
        rating: json['rating'] as String? ?? '0',
        reviewCount: json['reviewCount'] as int? ?? 0,
        stock: json['stock'] as int? ?? 0,
        redirect: json['redirect'] as bool? ?? true,
        brand: json['brand'] as String? ?? '',
        installmentText: json['installmentText'] as String? ?? '',
        shippingText: json['shippingText'] as String? ?? '',
        variations: (json['variations'] as List<dynamic>? ?? []).cast<String>(),
        reviews: (json['reviews'] as List<dynamic>? ?? []).map((entry) {
          final review = Map<String, dynamic>.from(entry as Map);
          return ProductReview(
            review['author'] as String? ?? 'Comprador',
            review['date'] as String? ?? '',
            review['comment'] as String? ?? '',
            review['stars'] as int? ?? 0,
          );
        }).toList(),
      );

  ProductPageData withRedirect(bool value) => ProductPageData(
    id: id,
    slug: slug,
    sourceUrl: sourceUrl,
    importNotice: importNotice,
    storeName: storeName,
    name: name,
    price: price,
    market: market,
    images: images,
    description: description,
    reviews: reviews,
    rating: rating,
    reviewCount: reviewCount,
    stock: stock,
    redirect: value,
    brand: brand,
    installmentText: installmentText,
    shippingText: shippingText,
    variations: variations,
  );

  ProductPageData edited({
    String? name,
    String? price,
    String? description,
    String? brand,
    String? storeName,
    String? shippingText,
    String? installmentText,
    int? stock,
    List<String>? variations,
    List<String>? images,
    List<ProductReview>? reviews,
  }) => ProductPageData(
    id: id,
    slug: slug,
    sourceUrl: sourceUrl,
    importNotice: importNotice,
    storeName: storeName ?? this.storeName,
    name: name ?? this.name,
    price: price ?? this.price,
    market: market,
    images: images ?? this.images,
    description: description ?? this.description,
    reviews: reviews ?? this.reviews,
    rating: rating,
    reviewCount: reviewCount,
    stock: stock ?? this.stock,
    redirect: redirect,
    brand: brand ?? this.brand,
    installmentText: installmentText ?? this.installmentText,
    shippingText: shippingText ?? this.shippingText,
    variations: variations ?? this.variations,
  );

  Map<String, dynamic> editableJson() => {
    'name': name,
    'price': price,
    'description': description,
    'brand': brand,
    'storeName': storeName,
    'stock': stock,
    'shippingText': shippingText,
    'installmentText': installmentText,
    'variations': variations,
    'images': images,
    'reviews': reviews
        .map(
          (review) => {
            'author': review.author,
            'date': review.date,
            'comment': review.comment,
            'stars': review.stars,
          },
        )
        .toList(),
  };
}

const demoProductPage = ProductPageData(
  name: 'Fone Bluetooth Wave Pro',
  price: 'R\$ 289,90',
  market: 'Mercado Livre',
  images: [
    'https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=900&q=85',
    'https://images.unsplash.com/photo-1546435770-a3e426bf472b?w=900&q=85',
    'https://images.unsplash.com/photo-1484704849700-f032a568e944?w=900&q=85',
  ],
  description:
      'Fone Bluetooth com som envolvente, almofadas confortáveis e bateria para acompanhar sua rotina. Confira as fotos e os detalhes antes de comprar.',
  variations: ['Grafite', 'Branco', 'Azul'],
  rating: '4,8',
  reviewCount: 2341,
  stock: 42,
  reviews: [
    ProductReview(
      'Mariana S.',
      '12 de setembro',
      'Som muito bom e confortável para usar durante o trabalho.',
      5,
    ),
    ProductReview(
      'Rafael M.',
      '8 de setembro',
      'A bateria dura bem. Gostei do acabamento e do encaixe.',
      5,
    ),
    ProductReview(
      'Camila R.',
      '2 de setembro',
      'Entrega rápida e produto igual ao anunciado.',
      4,
    ),
  ],
);

class CommerceShell extends StatefulWidget {
  const CommerceShell({this.onLogout, super.key});
  final VoidCallback? onLogout;
  @override
  State<CommerceShell> createState() => _CommerceShellState();
}

class _CommerceShellState extends State<CommerceShell> {
  int selected = 0;
  bool collapsed = false;
  ProductPageData? publishedPage;
  List<ProductPageData> publishedPages = [];
  static const nav = [
    ('Visão geral', Icons.grid_view_rounded),
    ('Produtos', Icons.inventory_2_outlined),
    ('Pedidos', Icons.receipt_long_outlined),
    ('Analytics', Icons.query_stats_rounded),
    ('Configurações', Icons.tune_rounded),
  ];

  @override
  void initState() {
    super.initState();
    if (selfHosted) loadPublishedPages();
  }

  Future<void> loadPublishedPages() async {
    try {
      final response = await http.get(Uri.base.resolve('/api/products'));
      if (response.statusCode != 200) return;
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final pages = (data['products'] as List<dynamic>)
          .map(
            (entry) => ProductPageData.fromJson(
              Map<String, dynamic>.from(entry as Map),
            ),
          )
          .toList();
      if (mounted) setState(() => publishedPages = pages);
    } catch (_) {
      // O catálogo continua acessível se a API estiver temporariamente fora do ar.
    }
  }

  @override
  Widget build(BuildContext context) {
    final compact = MediaQuery.sizeOf(context).width < 880;
    final pages = [
      if (selfHosted)
        ProductionOverview(
          publishedCount: publishedPages.length,
          onImport: () => openImporter(context),
        )
      else
        Dashboard(
          onNavigate: (i) => setState(() => selected = i),
          onImport: () => openImporter(context),
        ),
      Catalog(
        onImport: () => openImporter(context),
        onPreview: () => openStore(context),
        onEdit: (page) => openImporter(context, page),
        publishedPages: publishedPages,
      ),
      selfHosted ? const ProductionOrders() : const Orders(),
      const Analytics(),
      selfHosted ? const ServerSettings() : const Settings(),
    ];
    return Scaffold(
      body: SafeArea(
        child: Row(
          children: [
            if (!compact)
              Sidebar(
                selected: selected,
                collapsed: collapsed,
                onCollapse: () => setState(() => collapsed = !collapsed),
                onSelected: (i) => setState(() => selected = i),
              ),
            Expanded(
              child: Column(
                children: [
                  Topbar(
                    title: nav[selected].$1,
                    compact: compact,
                    onImport: () => openImporter(context),
                    onPreview: () => openStore(context),
                    onLogout: widget.onLogout,
                  ),
                  Expanded(
                    child: IndexedStack(index: selected, children: pages),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
      bottomNavigationBar: compact
          ? NavigationBar(
              height: 68,
              selectedIndex: selected,
              onDestinationSelected: (i) => setState(() => selected = i),
              backgroundColor: panel,
              indicatorColor: green.withValues(alpha: .16),
              destinations: nav
                  .map(
                    (e) => NavigationDestination(
                      icon: Icon(e.$2, size: 20),
                      label: e.$1 == 'Configurações' ? 'Ajustes' : e.$1,
                    ),
                  )
                  .toList(),
            )
          : null,
      floatingActionButton: compact
          ? FloatingActionButton(
              onPressed: () => openImporter(context),
              backgroundColor: green,
              foregroundColor: ink,
              child: const Icon(Icons.add_link),
            )
          : null,
    );
  }

  Future<void> openImporter(
    BuildContext context, [
    ProductPageData? initial,
  ]) async {
    final result = await showDialog<ProductPageData>(
      context: context,
      barrierDismissible: false,
      builder: (_) => ImportDialog(initialPage: initial),
    );
    if (!context.mounted || result == null) return;
    setState(() {
      publishedPage = result;
      publishedPages.removeWhere((page) => page.id == result.id);
      publishedPages.insert(0, result);
      selected = 1;
    });
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          selfHosted
              ? 'Página publicada no servidor.'
              : 'Página demonstrativa salva nesta sessão.',
        ),
      ),
    );
  }

  void openStore(BuildContext context) {
    if (selfHosted && publishedPages.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Publique um produto para visualizar sua loja.'),
        ),
      );
      return;
    }
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => Storefront(
          page:
              publishedPage ??
              (selfHosted ? publishedPages.first : demoProductPage),
        ),
      ),
    );
  }
}

class Logo extends StatelessWidget {
  const Logo({super.key});
  @override
  Widget build(BuildContext context) => Container(
    width: 39,
    height: 39,
    decoration: BoxDecoration(
      color: green,
      borderRadius: BorderRadius.circular(12),
    ),
    child: const Icon(Icons.change_history_rounded, color: ink),
  );
}

class Sidebar extends StatelessWidget {
  const Sidebar({
    required this.selected,
    required this.collapsed,
    required this.onCollapse,
    required this.onSelected,
    super.key,
  });
  final int selected;
  final bool collapsed;
  final VoidCallback onCollapse;
  final ValueChanged<int> onSelected;
  @override
  Widget build(BuildContext context) => AnimatedContainer(
    duration: const Duration(milliseconds: 220),
    width: collapsed ? 86 : 244,
    padding: const EdgeInsets.fromLTRB(16, 22, 16, 16),
    decoration: const BoxDecoration(
      color: Color(0xFF0B1627),
      border: Border(right: BorderSide(color: line)),
    ),
    child: Column(
      children: [
        Row(
          mainAxisAlignment: collapsed
              ? MainAxisAlignment.center
              : MainAxisAlignment.start,
          children: [
            const Logo(),
            if (!collapsed) ...[
              const SizedBox(width: 11),
              const Text(
                'nexo',
                style: TextStyle(fontSize: 22, fontWeight: FontWeight.w900),
              ),
              const Spacer(),
            ],
          ],
        ),
        const SizedBox(height: 36),
        ...List.generate(_CommerceShellState.nav.length, (i) {
          final item = _CommerceShellState.nav[i];
          final active = selected == i;
          return Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Material(
              color: active ? green.withValues(alpha: .12) : Colors.transparent,
              borderRadius: BorderRadius.circular(12),
              child: InkWell(
                onTap: () => onSelected(i),
                borderRadius: BorderRadius.circular(12),
                child: SizedBox(
                  height: 48,
                  child: Row(
                    mainAxisAlignment: collapsed
                        ? MainAxisAlignment.center
                        : MainAxisAlignment.start,
                    children: [
                      if (!collapsed) const SizedBox(width: 14),
                      Icon(item.$2, color: active ? green : muted, size: 21),
                      if (!collapsed) ...[
                        const SizedBox(width: 13),
                        Flexible(
                          child: Text(
                            item.$1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              color: active ? Colors.white : muted,
                              fontWeight: active
                                  ? FontWeight.w700
                                  : FontWeight.w500,
                            ),
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
              ),
            ),
          );
        }),
        const Spacer(),
        if (!collapsed && !selfHosted)
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: soft,
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: line),
            ),
            child: const Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(Icons.bolt, color: orange, size: 18),
                    SizedBox(width: 7),
                    Text(
                      'Plano Pro',
                      style: TextStyle(fontWeight: FontWeight.w800),
                    ),
                  ],
                ),
                SizedBox(height: 7),
                Text(
                  '2.480 de 5.000 visitas',
                  style: TextStyle(color: muted, fontSize: 12),
                ),
                SizedBox(height: 9),
                LinearProgressIndicator(
                  value: .496,
                  minHeight: 5,
                  color: green,
                  backgroundColor: line,
                ),
              ],
            ),
          ),
        const SizedBox(height: 12),
        IconButton(
          onPressed: onCollapse,
          icon: Icon(collapsed ? Icons.last_page : Icons.first_page),
          color: muted,
        ),
      ],
    ),
  );
}

class Topbar extends StatelessWidget {
  const Topbar({
    required this.title,
    required this.compact,
    required this.onImport,
    required this.onPreview,
    this.onLogout,
    super.key,
  });
  final String title;
  final bool compact;
  final VoidCallback onImport, onPreview;
  final VoidCallback? onLogout;
  @override
  Widget build(BuildContext context) => Container(
    height: 76,
    padding: EdgeInsets.symmetric(horizontal: compact ? 17 : 28),
    decoration: const BoxDecoration(
      border: Border(bottom: BorderSide(color: line)),
    ),
    child: Row(
      children: [
        if (compact) ...[const Logo(), const SizedBox(width: 12)],
        Text(
          title,
          style: const TextStyle(fontSize: 19, fontWeight: FontWeight.w800),
        ),
        const Spacer(),
        if (!compact) ...[
          OutlinedButton.icon(
            onPressed: onPreview,
            icon: const Icon(Icons.open_in_new, size: 17),
            label: const Text('Ver loja'),
          ),
          const SizedBox(width: 10),
          FilledButton.icon(
            onPressed: onImport,
            icon: const Icon(Icons.add_link, size: 18),
            label: const Text('Importar produto'),
            style: FilledButton.styleFrom(
              backgroundColor: green,
              foregroundColor: ink,
              padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
            ),
          ),
          const SizedBox(width: 20),
        ],
        if (!selfHosted)
          Badge(
            backgroundColor: red,
            smallSize: 8,
            child: IconButton(
              onPressed: () {},
              icon: const Icon(Icons.notifications_none),
              color: muted,
            ),
          ),
        const SizedBox(width: 8),
        if (selfHosted)
          PopupMenuButton<String>(
            tooltip: 'Conta',
            onSelected: (value) {
              if (value == 'logout') onLogout?.call();
            },
            itemBuilder: (_) => const [
              PopupMenuItem<String>(
                enabled: false,
                child: Text('Administrador'),
              ),
              PopupMenuDivider(),
              PopupMenuItem<String>(
                value: 'logout',
                child: Row(
                  children: [
                    Icon(Icons.logout_rounded, size: 18),
                    SizedBox(width: 10),
                    Text('Sair'),
                  ],
                ),
              ),
            ],
            child: const CircleAvatar(
              radius: 18,
              backgroundColor: purple,
              child: Text(
                'A',
                style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800),
              ),
            ),
          )
        else
          const CircleAvatar(
            radius: 18,
            backgroundColor: purple,
            child: Text(
              'EF',
              style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800),
            ),
          ),
      ],
    ),
  );
}

class Heading extends StatelessWidget {
  const Heading(
    this.eyebrow,
    this.title,
    this.subtitle, {
    this.trailing,
    super.key,
  });
  final String eyebrow, title, subtitle;
  final Widget? trailing;
  @override
  Widget build(BuildContext context) => Wrap(
    spacing: 24,
    runSpacing: 16,
    alignment: WrapAlignment.spaceBetween,
    crossAxisAlignment: WrapCrossAlignment.end,
    children: [
      Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            eyebrow,
            style: const TextStyle(
              color: green,
              fontSize: 11,
              fontWeight: FontWeight.w800,
              letterSpacing: 1.2,
            ),
          ),
          const SizedBox(height: 7),
          Text(title, style: Theme.of(context).textTheme.displaySmall),
          const SizedBox(height: 7),
          Text(subtitle, style: const TextStyle(color: muted)),
        ],
      ),
      if (trailing != null) trailing!,
    ],
  );
}

class Content extends StatelessWidget {
  const Content({required this.child, this.maxWidth = 1400, super.key});
  final Widget child;
  final double maxWidth;
  @override
  Widget build(BuildContext context) => SingleChildScrollView(
    padding: const EdgeInsets.fromLTRB(26, 26, 26, 44),
    child: Center(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: maxWidth),
        child: child,
      ),
    ),
  );
}

class ProductionOverview extends StatelessWidget {
  const ProductionOverview({
    required this.publishedCount,
    required this.onImport,
    super.key,
  });

  final int publishedCount;
  final VoidCallback onImport;

  @override
  Widget build(BuildContext context) => Content(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Heading(
          'VISÃO GERAL',
          'Sua operação',
          '$publishedCount produto(s) publicado(s) no seu servidor.',
          trailing: FilledButton.icon(
            onPressed: onImport,
            icon: const Icon(Icons.add_link),
            label: const Text('Importar produto'),
          ),
        ),
        const SizedBox(height: 24),
        const TrafficMap(),
      ],
    ),
  );
}

class ProductionOrders extends StatelessWidget {
  const ProductionOrders({super.key});

  @override
  Widget build(BuildContext context) => const Content(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Heading(
          'PEDIDOS',
          'Pedidos',
          'As compras são concluídas no Mercado Livre.',
        ),
        SizedBox(height: 24),
        Card(
          child: Padding(
            padding: EdgeInsets.all(24),
            child: Text(
              'Este servidor ainda não recebe os pedidos do Mercado Livre. Consulte os pedidos na sua conta do marketplace.',
              style: TextStyle(color: muted),
            ),
          ),
        ),
      ],
    ),
  );
}

class ServerSettings extends StatelessWidget {
  const ServerSettings({super.key});

  @override
  Widget build(BuildContext context) => Content(
    maxWidth: 1000,
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Heading(
          'PREFERÊNCIAS',
          'Configurações',
          'Estado da integração hospedada no seu servidor.',
        ),
        const SizedBox(height: 24),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: FutureBuilder<http.Response>(
              future: http.get(Uri.base.resolve('/api/mercadolivre/status')),
              builder: (context, result) {
                if (result.hasError) {
                  return const Text('Não foi possível consultar a integração.');
                }
                if (!result.hasData) {
                  return const Text('Consultando a integração…');
                }
                if (result.data!.statusCode != 200) {
                  return const Text('Não foi possível consultar a integração.');
                }
                final status =
                    jsonDecode(result.data!.body) as Map<String, dynamic>;
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Mercado Livre',
                      style: TextStyle(
                        fontSize: 19,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    const SizedBox(height: 10),
                    Text(
                      status['connected'] == true
                          ? 'Conta conectada para importar produtos.'
                          : status['configured'] == true
                          ? 'Aplicativo configurado. Conecte sua conta para importar.'
                          : 'Cadastre as credenciais do aplicativo no arquivo .env do servidor.',
                      style: const TextStyle(color: muted),
                    ),
                    if ((status['error'] as String? ?? '').isNotEmpty) ...[
                      const SizedBox(height: 10),
                      Text(
                        status['error'] as String,
                        style: const TextStyle(color: red, fontSize: 13),
                      ),
                    ],
                    if ((status['warning'] as String? ?? '').isNotEmpty) ...[
                      const SizedBox(height: 10),
                      Text(
                        status['warning'] as String,
                        style: const TextStyle(color: orange, fontSize: 13),
                      ),
                    ],
                    if (status['configured'] == true &&
                        status['connected'] != true) ...[
                      const SizedBox(height: 16),
                      FilledButton.icon(
                        onPressed: () => launchUrl(
                          Uri.base.resolve('/api/mercadolivre/connect'),
                          mode: LaunchMode.platformDefault,
                          webOnlyWindowName: '_self',
                        ),
                        icon: const Icon(Icons.link),
                        label: const Text('Conectar conta'),
                      ),
                    ],
                  ],
                );
              },
            ),
          ),
        ),
      ],
    ),
  );
}

class Dashboard extends StatelessWidget {
  const Dashboard({
    required this.onNavigate,
    required this.onImport,
    super.key,
  });
  final ValueChanged<int> onNavigate;
  final VoidCallback onImport;
  @override
  Widget build(BuildContext context) => Content(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Heading(
          'VISÃO GERAL',
          'Olá, Edson 👋',
          selfHosted
              ? 'Indicadores de vendas demonstrativos. O tráfego real está em Analytics.'
              : 'Sua operação está saudável. Você vendeu 18% a mais que no período anterior.',
          trailing: OutlinedButton.icon(
            onPressed: () {},
            icon: const Icon(Icons.calendar_today_outlined, size: 17),
            label: const Text('Últimos 30 dias'),
          ),
        ),
        const SizedBox(height: 24),
        const Metrics(),
        const SizedBox(height: 20),
        LayoutBuilder(
          builder: (_, c) => c.maxWidth < 900
              ? const Column(
                  children: [Revenue(), SizedBox(height: 20), Funnel()],
                )
              : const Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(flex: 2, child: Revenue()),
                    SizedBox(width: 20),
                    Expanded(child: Funnel()),
                  ],
                ),
        ),
        const SizedBox(height: 20),
        LayoutBuilder(
          builder: (_, c) {
            final orders = RecentOrders(onAll: () => onNavigate(2));
            return c.maxWidth < 980
                ? Column(
                    children: [
                      orders,
                      const SizedBox(height: 20),
                      const TopProducts(),
                    ],
                  )
                : Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(flex: 3, child: orders),
                      const SizedBox(width: 20),
                      const Expanded(flex: 2, child: TopProducts()),
                    ],
                  );
          },
        ),
        const SizedBox(height: 20),
        Container(
          padding: const EdgeInsets.all(24),
          decoration: BoxDecoration(
            gradient: LinearGradient(
              colors: [
                purple.withValues(alpha: .24),
                green.withValues(alpha: .08),
              ],
            ),
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: purple.withValues(alpha: .4)),
          ),
          child: Wrap(
            spacing: 20,
            runSpacing: 16,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Container(
                width: 48,
                height: 48,
                decoration: BoxDecoration(
                  color: purple,
                  borderRadius: BorderRadius.circular(14),
                ),
                child: const Icon(Icons.rocket_launch_outlined),
              ),
              const SizedBox(
                width: 520,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Publique seu próximo campeão de vendas',
                      style: TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    SizedBox(height: 5),
                    Text(
                      'Cole o link do marketplace. A Nexo organiza imagens, variações e informações para você revisar.',
                      style: TextStyle(color: muted),
                    ),
                  ],
                ),
              ),
              FilledButton.icon(
                onPressed: onImport,
                icon: const Icon(Icons.add_link),
                label: const Text('Importar agora'),
                style: FilledButton.styleFrom(
                  backgroundColor: Colors.white,
                  foregroundColor: ink,
                ),
              ),
            ],
          ),
        ),
      ],
    ),
  );
}

class Metrics extends StatelessWidget {
  const Metrics({super.key});
  @override
  Widget build(BuildContext context) {
    const data = [
      (
        'Faturamento',
        'R\$ 48.292,40',
        '+18,4%',
        Icons.payments_outlined,
        green,
      ),
      ('Lucro líquido', 'R\$ 17.841,12', '+12,7%', Icons.trending_up, purple),
      ('Pedidos', '384', '+8,2%', Icons.shopping_bag_outlined, orange),
      ('Conversão', '4,82%', '+0,6%', Icons.ads_click, Color(0xFF5DD8F7)),
    ];
    return LayoutBuilder(
      builder: (_, c) {
        final cols = c.maxWidth >= 1080
            ? 4
            : c.maxWidth >= 580
            ? 2
            : 1;
        final width = (c.maxWidth - (cols - 1) * 14) / cols;
        return Wrap(
          spacing: 14,
          runSpacing: 14,
          children: data
              .map(
                (e) => SizedBox(
                  width: width,
                  child: Card(
                    child: Padding(
                      padding: const EdgeInsets.all(18),
                      child: Row(
                        children: [
                          Container(
                            width: 44,
                            height: 44,
                            decoration: BoxDecoration(
                              color: e.$5.withValues(alpha: .12),
                              borderRadius: BorderRadius.circular(12),
                            ),
                            child: Icon(e.$4, color: e.$5, size: 21),
                          ),
                          const SizedBox(width: 13),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  e.$1,
                                  style: const TextStyle(
                                    color: muted,
                                    fontSize: 12,
                                  ),
                                ),
                                const SizedBox(height: 4),
                                FittedBox(
                                  fit: BoxFit.scaleDown,
                                  alignment: Alignment.centerLeft,
                                  child: Text(
                                    e.$2,
                                    style: const TextStyle(
                                      fontSize: 20,
                                      fontWeight: FontWeight.w800,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          ),
                          Text(
                            e.$3,
                            style: const TextStyle(
                              color: green,
                              fontSize: 10,
                              fontWeight: FontWeight.w800,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              )
              .toList(),
        );
      },
    );
  }
}

class SectionTitle extends StatelessWidget {
  const SectionTitle(this.title, this.subtitle, {this.action, super.key});
  final String title, subtitle;
  final Widget? action;
  @override
  Widget build(BuildContext context) => Row(
    children: [
      Expanded(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 4),
            Text(subtitle, style: const TextStyle(color: muted, fontSize: 12)),
          ],
        ),
      ),
      if (action != null) action!,
    ],
  );
}

class Revenue extends StatelessWidget {
  const Revenue({super.key});
  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(22),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionTitle(
            'Receita ao longo do tempo',
            'Comparado ao período anterior',
          ),
          const SizedBox(height: 20),
          const Wrap(
            spacing: 18,
            runSpacing: 8,
            children: [
              Legend(green, 'Período atual'),
              Legend(muted, 'Período anterior'),
            ],
          ),
          const SizedBox(height: 12),
          const SizedBox(
            height: 220,
            width: double.infinity,
            child: CustomPaint(painter: ChartPainter()),
          ),
        ],
      ),
    ),
  );
}

class Legend extends StatelessWidget {
  const Legend(this.color, this.label, {super.key});
  final Color color;
  final String label;
  @override
  Widget build(BuildContext context) => Row(
    children: [
      Container(
        width: 8,
        height: 8,
        decoration: BoxDecoration(color: color, shape: BoxShape.circle),
      ),
      const SizedBox(width: 7),
      Text(label, style: const TextStyle(color: muted, fontSize: 11)),
    ],
  );
}

class ChartPainter extends CustomPainter {
  const ChartPainter();
  @override
  void paint(Canvas canvas, Size size) {
    final grid = Paint()
      ..color = line
      ..strokeWidth = 1;
    for (var i = 0; i < 5; i++) {
      canvas.drawLine(
        Offset(0, size.height * i / 4),
        Offset(size.width, size.height * i / 4),
        grid,
      );
    }
    draw(
      canvas,
      size,
      [.82, .74, .78, .68, .72, .61, .66, .48, .54, .43, .47, .34],
      muted,
      false,
    );
    draw(
      canvas,
      size,
      [.78, .7, .76, .55, .62, .43, .49, .28, .34, .18, .24, .08],
      green,
      true,
    );
  }

  void draw(
    Canvas canvas,
    Size size,
    List<double> points,
    Color color,
    bool fill,
  ) {
    final path = Path();
    for (var i = 0; i < points.length; i++) {
      final p = Offset(
        size.width * i / (points.length - 1),
        size.height * points[i],
      );
      i == 0 ? path.moveTo(p.dx, p.dy) : path.lineTo(p.dx, p.dy);
    }
    if (fill) {
      final area = Path.from(path)
        ..lineTo(size.width, size.height)
        ..lineTo(0, size.height)
        ..close();
      canvas.drawPath(
        area,
        Paint()
          ..shader = LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [green.withValues(alpha: .22), green.withValues(alpha: 0)],
          ).createShader(Offset.zero & size),
      );
    }
    canvas.drawPath(
      path,
      Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeWidth = fill ? 2.5 : 1.5
        ..strokeCap = StrokeCap.round,
    );
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class Funnel extends StatelessWidget {
  const Funnel({super.key});
  @override
  Widget build(BuildContext context) {
    const rows = [
      ('Visitantes', '8.921', 1.0, purple),
      ('Produto visto', '5.487', .78, Color(0xFF5DD8F7)),
      ('Carrinho', '1.102', .54, orange),
      ('Checkout', '681', .36, green),
      ('Compra', '430', .22, red),
    ];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(22),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionTitle('Funil de vendas', 'Conversão geral de 4,82%'),
            const SizedBox(height: 23),
            ...rows.map(
              (e) => Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: Column(
                  children: [
                    Row(
                      children: [
                        Text(e.$1, style: const TextStyle(fontSize: 12)),
                        const Spacer(),
                        Text(
                          e.$2,
                          style: const TextStyle(
                            fontWeight: FontWeight.w800,
                            fontSize: 12,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: FractionallySizedBox(
                        widthFactor: e.$3,
                        child: Container(
                          height: 8,
                          decoration: BoxDecoration(
                            color: e.$4,
                            borderRadius: BorderRadius.circular(6),
                          ),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class Status extends StatelessWidget {
  const Status(this.label, this.color, {super.key});
  final String label;
  final Color color;
  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
    decoration: BoxDecoration(
      color: color.withValues(alpha: .1),
      borderRadius: BorderRadius.circular(20),
    ),
    child: Text(
      label,
      style: TextStyle(color: color, fontSize: 10, fontWeight: FontWeight.w800),
    ),
  );
}

class RecentOrders extends StatelessWidget {
  const RecentOrders({required this.onAll, super.key});
  final VoidCallback onAll;
  @override
  Widget build(BuildContext context) {
    const orders = [
      ('#NX-1048', 'Marina Costa', 'R\$ 289,90', 'Pago', green),
      ('#NX-1047', 'Lucas Mendes', 'R\$ 149,80', 'Pendente', orange),
      ('#NX-1046', 'Clara Souza', 'R\$ 619,00', 'Pago', green),
      ('#NX-1045', 'Rafael Lima', 'R\$ 89,90', 'Cancelado', red),
    ];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(22),
        child: Column(
          children: [
            SectionTitle(
              'Pedidos recentes',
              'Atualização em tempo real',
              action: TextButton(
                onPressed: onAll,
                child: const Text('Ver todos'),
              ),
            ),
            const SizedBox(height: 12),
            ...orders.map(
              (e) => Container(
                padding: const EdgeInsets.symmetric(vertical: 13),
                decoration: const BoxDecoration(
                  border: Border(bottom: BorderSide(color: line)),
                ),
                child: Row(
                  children: [
                    Container(
                      width: 36,
                      height: 36,
                      decoration: BoxDecoration(
                        color: soft,
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: const Icon(
                        Icons.shopping_bag_outlined,
                        size: 18,
                        color: muted,
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            e.$2,
                            style: const TextStyle(
                              fontWeight: FontWeight.w700,
                              fontSize: 13,
                            ),
                          ),
                          Text(
                            e.$1,
                            style: const TextStyle(color: muted, fontSize: 11),
                          ),
                        ],
                      ),
                    ),
                    Text(
                      e.$3,
                      style: const TextStyle(
                        fontWeight: FontWeight.w800,
                        fontSize: 13,
                      ),
                    ),
                    const SizedBox(width: 14),
                    Status(e.$4, e.$5),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class TopProducts extends StatelessWidget {
  const TopProducts({super.key});
  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(22),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionTitle('Produtos em destaque', 'Por faturamento'),
          const SizedBox(height: 18),
          ...products
              .take(3)
              .map(
                (p) => Padding(
                  padding: const EdgeInsets.only(bottom: 16),
                  child: Row(
                    children: [
                      Thumb(p.image, 48),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              p.name,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                fontWeight: FontWeight.w700,
                                fontSize: 12,
                              ),
                            ),
                            Text(
                              '${p.sales} vendas',
                              style: const TextStyle(
                                color: muted,
                                fontSize: 11,
                              ),
                            ),
                          ],
                        ),
                      ),
                      Text(
                        p.revenue,
                        style: const TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
        ],
      ),
    ),
  );
}

class Thumb extends StatelessWidget {
  const Thumb(this.url, this.size, {super.key});
  final String url;
  final double size;
  @override
  Widget build(BuildContext context) => ClipRRect(
    borderRadius: BorderRadius.circular(11),
    child: Image.network(
      url,
      width: size,
      height: size,
      fit: BoxFit.cover,
      errorBuilder: (_, __, ___) => Container(
        width: size,
        height: size,
        color: soft,
        child: const Icon(Icons.image_outlined, color: muted),
      ),
    ),
  );
}

class Catalog extends StatefulWidget {
  const Catalog({
    required this.onImport,
    required this.onPreview,
    required this.onEdit,
    this.publishedPages = const [],
    super.key,
  });
  final VoidCallback onImport, onPreview;
  final ValueChanged<ProductPageData> onEdit;
  final List<ProductPageData> publishedPages;
  @override
  State<Catalog> createState() => _CatalogState();
}

class _CatalogState extends State<Catalog> {
  String query = '', filter = 'Todos';
  @override
  Widget build(BuildContext context) {
    if (selfHosted) return liveCatalog();
    final shown = products
        .where(
          (p) =>
              p.name.toLowerCase().contains(query.toLowerCase()) &&
              (filter == 'Todos' ||
                  (filter == 'Ativos' ? p.active : !p.active)),
        )
        .toList();
    return Content(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Heading(
            'CATÁLOGO',
            'Seus produtos',
            '${products.length} produtos • 4 publicados • 1 rascunho',
            trailing: FilledButton.icon(
              onPressed: widget.onImport,
              icon: const Icon(Icons.add),
              label: const Text('Novo produto'),
            ),
          ),
          const SizedBox(height: 24),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                children: [
                  Wrap(
                    spacing: 12,
                    runSpacing: 12,
                    children: [
                      SizedBox(
                        width: 330,
                        child: TextField(
                          onChanged: (v) => setState(() => query = v),
                          decoration: const InputDecoration(
                            prefixIcon: Icon(Icons.search),
                            hintText: 'Buscar por produto…',
                          ),
                        ),
                      ),
                      ...['Todos', 'Ativos', 'Rascunhos'].map(
                        (f) => ChoiceChip(
                          label: Text(f),
                          selected: filter == f,
                          onSelected: (_) => setState(() => filter = f),
                          selectedColor: green.withValues(alpha: .18),
                          side: const BorderSide(color: line),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 18),
                  LayoutBuilder(
                    builder: (_, c) {
                      final cols = c.maxWidth >= 1050
                          ? 3
                          : c.maxWidth >= 620
                          ? 2
                          : 1;
                      final width = (c.maxWidth - (cols - 1) * 16) / cols;
                      return Wrap(
                        spacing: 16,
                        runSpacing: 16,
                        children: shown
                            .map(
                              (p) => SizedBox(
                                width: width,
                                child: ProductCard(p, widget.onPreview),
                              ),
                            )
                            .toList(),
                      );
                    },
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget liveCatalog() {
    final pages = widget.publishedPages.where(
      (page) => page.name.toLowerCase().contains(query.toLowerCase()),
    );
    return Content(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Heading(
            'CATÁLOGO',
            'Seus produtos',
            '${widget.publishedPages.length} página(s) publicada(s) no servidor',
            trailing: FilledButton.icon(
              onPressed: widget.onImport,
              icon: const Icon(Icons.add),
              label: const Text('Novo produto'),
            ),
          ),
          const SizedBox(height: 20),
          TextField(
            onChanged: (value) => setState(() => query = value),
            decoration: const InputDecoration(
              prefixIcon: Icon(Icons.search),
              hintText: 'Buscar por produto…',
            ),
          ),
          const SizedBox(height: 20),
          if (widget.publishedPages.isEmpty)
            const Card(
              child: Padding(
                padding: EdgeInsets.all(24),
                child: Text(
                  'Nenhum produto publicado ainda. Importe um link para começar.',
                ),
              ),
            ),
          ...pages.map(
            (page) => Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Card(
                child: ListTile(
                  contentPadding: const EdgeInsets.all(12),
                  leading: Thumb(page.images.first, 54),
                  title: Text(
                    page.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  subtitle: Text('${page.price} • ${page.market}'),
                  trailing: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      IconButton(
                        tooltip: 'Editar sua página',
                        onPressed: () => widget.onEdit(page),
                        icon: const Icon(Icons.edit_outlined),
                      ),
                      IconButton(
                        tooltip: 'Abrir página pública',
                        onPressed: () => launchUrl(
                          Uri.base.resolve('/p/${page.slug}'),
                          mode: LaunchMode.externalApplication,
                        ),
                        icon: const Icon(Icons.open_in_new),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class ProductCard extends StatelessWidget {
  const ProductCard(this.product, this.onPreview, {super.key});
  final Product product;
  final VoidCallback onPreview;
  @override
  Widget build(BuildContext context) => Container(
    decoration: BoxDecoration(
      color: ink.withValues(alpha: .55),
      borderRadius: BorderRadius.circular(16),
      border: Border.all(color: line),
    ),
    clipBehavior: Clip.antiAlias,
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Stack(
          children: [
            SizedBox(
              width: double.infinity,
              height: 150,
              child: Image.network(
                product.image,
                fit: BoxFit.cover,
                errorBuilder: (_, __, ___) => const ColoredBox(
                  color: soft,
                  child: Icon(Icons.image_not_supported_outlined),
                ),
              ),
            ),
            Positioned(
              top: 10,
              left: 10,
              child: Status(
                product.active ? 'Publicado' : 'Rascunho',
                product.active ? green : muted,
              ),
            ),
            Positioned(
              top: 8,
              right: 8,
              child: IconButton.filledTonal(
                onPressed: () {},
                icon: const Icon(Icons.more_horiz),
                style: IconButton.styleFrom(
                  backgroundColor: ink.withValues(alpha: .8),
                ),
              ),
            ),
          ],
        ),
        Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                product.market.toUpperCase(),
                style: const TextStyle(
                  color: purple,
                  fontSize: 10,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 1,
                ),
              ),
              const SizedBox(height: 6),
              Text(
                product.name,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontWeight: FontWeight.w800),
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  Text(
                    product.price,
                    style: const TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.w900,
                    ),
                  ),
                  const Spacer(),
                  Icon(
                    product.mode == 'Checkout próprio'
                        ? Icons.credit_card
                        : Icons.open_in_new,
                    color: muted,
                    size: 15,
                  ),
                  const SizedBox(width: 5),
                  Flexible(
                    child: Text(
                      product.mode,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: muted, fontSize: 10),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 14),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton(
                      onPressed: () =>
                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(
                              content: Text(
                                'Editor visual aberto em modo demonstração.',
                              ),
                            ),
                          ),
                      child: const Text('Editar'),
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton.outlined(
                    onPressed: onPreview,
                    icon: const Icon(Icons.visibility_outlined, size: 19),
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    ),
  );
}

class Orders extends StatefulWidget {
  const Orders({super.key});
  @override
  State<Orders> createState() => _OrdersState();
}

class _OrdersState extends State<Orders> {
  String filter = 'Todos';
  static const data = [
    (
      '#NX-1048',
      'Marina Costa',
      'Fone Bluetooth Wave Pro',
      '20 set, 14:32',
      'R\$ 289,90',
      'Pago',
      green,
    ),
    (
      '#NX-1047',
      'Lucas Mendes',
      'Mochila Essentials 24L',
      '20 set, 13:08',
      'R\$ 149,80',
      'Pendente',
      orange,
    ),
    (
      '#NX-1046',
      'Clara Souza',
      'Smartwatch Active S9',
      '20 set, 11:46',
      'R\$ 619,00',
      'Pago',
      green,
    ),
    (
      '#NX-1045',
      'Rafael Lima',
      'Luminária Minimal Desk',
      '19 set, 22:15',
      'R\$ 89,90',
      'Cancelado',
      red,
    ),
    (
      '#NX-1044',
      'Bianca Alves',
      'Tênis Urban Runner',
      '19 set, 18:51',
      'R\$ 379,80',
      'Enviado',
      Color(0xFF5DD8F7),
    ),
    (
      '#NX-1043',
      'João Martins',
      'Fone Bluetooth Wave Pro',
      '19 set, 16:20',
      'R\$ 289,90',
      'Reembolsado',
      purple,
    ),
  ];
  @override
  Widget build(BuildContext context) {
    final shown = data.where((e) => filter == 'Todos' || e.$6 == filter);
    return Content(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Heading(
            'OPERAÇÃO',
            'Pedidos',
            'Acompanhe pagamentos, envios e pós-venda em um só lugar.',
          ),
          const SizedBox(height: 24),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Column(
                children: [
                  Align(
                    alignment: Alignment.centerLeft,
                    child: Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children:
                          ['Todos', 'Pago', 'Pendente', 'Enviado', 'Cancelado']
                              .map(
                                (f) => ChoiceChip(
                                  label: Text(f),
                                  selected: filter == f,
                                  onSelected: (_) => setState(() => filter = f),
                                  selectedColor: green.withValues(alpha: .18),
                                  side: const BorderSide(color: line),
                                ),
                              )
                              .toList(),
                    ),
                  ),
                  const SizedBox(height: 16),
                  ...shown.map(
                    (e) => InkWell(
                      onTap: () => showOrder(context, e),
                      borderRadius: BorderRadius.circular(12),
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 12,
                          vertical: 15,
                        ),
                        decoration: const BoxDecoration(
                          border: Border(bottom: BorderSide(color: line)),
                        ),
                        child: Row(
                          children: [
                            Container(
                              width: 42,
                              height: 42,
                              decoration: BoxDecoration(
                                color: e.$7.withValues(alpha: .1),
                                borderRadius: BorderRadius.circular(12),
                              ),
                              child: Icon(
                                Icons.receipt_long_outlined,
                                color: e.$7,
                                size: 20,
                              ),
                            ),
                            const SizedBox(width: 13),
                            SizedBox(
                              width: 90,
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    e.$1,
                                    style: const TextStyle(
                                      fontWeight: FontWeight.w800,
                                    ),
                                  ),
                                  Text(
                                    e.$4,
                                    style: const TextStyle(
                                      color: muted,
                                      fontSize: 10,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    e.$2,
                                    style: const TextStyle(
                                      fontWeight: FontWeight.w700,
                                      fontSize: 13,
                                    ),
                                  ),
                                  Text(
                                    e.$3,
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(
                                      color: muted,
                                      fontSize: 11,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            Text(
                              e.$5,
                              style: const TextStyle(
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                            const SizedBox(width: 14),
                            Status(e.$6, e.$7),
                            const SizedBox(width: 4),
                            const Icon(Icons.chevron_right, color: muted),
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  void showOrder(
    BuildContext context,
    (String, String, String, String, String, String, Color) e,
  ) => showModalBottomSheet<void>(
    context: context,
    backgroundColor: panel,
    showDragHandle: true,
    builder: (_) => Padding(
      padding: const EdgeInsets.fromLTRB(24, 8, 24, 32),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                'Pedido ${e.$1}',
                style: Theme.of(context).textTheme.headlineSmall,
              ),
              const Spacer(),
              Status(e.$6, e.$7),
            ],
          ),
          const SizedBox(height: 24),
          InfoRow(Icons.person_outline, 'Cliente', e.$2),
          InfoRow(Icons.inventory_2_outlined, 'Produto', e.$3),
          InfoRow(Icons.payments_outlined, 'Total', e.$5),
          const InfoRow(
            Icons.pix,
            'Pagamento',
            'Pix • confirmado automaticamente',
          ),
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: FilledButton.icon(
              onPressed: () => Navigator.pop(context),
              icon: const Icon(Icons.local_shipping_outlined),
              label: const Text('Preparar envio'),
            ),
          ),
        ],
      ),
    ),
  );
}

class InfoRow extends StatelessWidget {
  const InfoRow(this.icon, this.label, this.value, {super.key});
  final IconData icon;
  final String label, value;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: 15),
    child: Row(
      children: [
        Icon(icon, color: muted, size: 20),
        const SizedBox(width: 12),
        Text(label, style: const TextStyle(color: muted)),
        const Spacer(),
        Flexible(
          child: Text(
            value,
            textAlign: TextAlign.end,
            style: const TextStyle(fontWeight: FontWeight.w700),
          ),
        ),
      ],
    ),
  );
}

class Analytics extends StatelessWidget {
  const Analytics({super.key});
  @override
  Widget build(BuildContext context) {
    const channels = [
      ('Direto', .42, 'R\$ 20.283'),
      ('Instagram', .28, 'R\$ 13.522'),
      ('Google', .19, 'R\$ 9.176'),
      ('Afiliados', .11, 'R\$ 5.311'),
    ];
    return Content(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Heading(
            'INTELIGÊNCIA',
            'Analytics',
            selfHosted
                ? 'Acompanhe visitas reais às páginas publicadas no seu servidor.'
                : 'Entenda o que gera resultado e onde otimizar.',
            trailing: selfHosted
                ? null
                : OutlinedButton.icon(
                    onPressed: () => ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(
                        content: Text(
                          'Relatório CSV preparado para exportação.',
                        ),
                      ),
                    ),
                    icon: const Icon(Icons.download_outlined),
                    label: const Text('Exportar CSV'),
                  ),
          ),
          const SizedBox(height: 24),
          if (!selfHosted) ...[const Metrics(), const SizedBox(height: 20)],
          const TrafficMap(),
          if (!selfHosted) ...[
            const SizedBox(height: 20),
            LayoutBuilder(
              builder: (_, c) {
                final revenue = Card(
                  child: Padding(
                    padding: const EdgeInsets.all(22),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const SectionTitle(
                          'Receita por canal',
                          'Participação no faturamento total',
                        ),
                        const SizedBox(height: 24),
                        ...channels.map(
                          (e) => Padding(
                            padding: const EdgeInsets.only(bottom: 18),
                            child: Column(
                              children: [
                                Row(
                                  children: [
                                    Text(e.$1),
                                    const Spacer(),
                                    Text(
                                      e.$3,
                                      style: const TextStyle(
                                        fontWeight: FontWeight.w800,
                                      ),
                                    ),
                                  ],
                                ),
                                const SizedBox(height: 8),
                                LinearProgressIndicator(
                                  value: e.$2,
                                  minHeight: 8,
                                  borderRadius: BorderRadius.circular(10),
                                  color: e.$1 == 'Direto' ? green : purple,
                                  backgroundColor: line,
                                ),
                              ],
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                );
                const pix = Card(
                  child: Padding(
                    padding: EdgeInsets.all(22),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        SectionTitle(
                          'Saúde do Pix',
                          'Cobranças nos últimos 30 dias',
                        ),
                        SizedBox(height: 24),
                        Donut(),
                        SizedBox(height: 20),
                        InfoRow(Icons.check_circle_outline, 'Pagos', '430'),
                        InfoRow(Icons.schedule, 'Pendentes', '71'),
                        InfoRow(Icons.timer_off_outlined, 'Expirados', '52'),
                      ],
                    ),
                  ),
                );
                return c.maxWidth < 850
                    ? Column(
                        children: [revenue, const SizedBox(height: 20), pix],
                      )
                    : Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Expanded(child: revenue),
                          const SizedBox(width: 20),
                          const Expanded(child: pix),
                        ],
                      );
              },
            ),
          ],
        ],
      ),
    );
  }
}

class Donut extends StatelessWidget {
  const Donut({super.key});
  @override
  Widget build(BuildContext context) => Center(
    child: SizedBox(
      width: 150,
      height: 150,
      child: Stack(
        alignment: Alignment.center,
        children: [
          const SizedBox.expand(
            child: CircularProgressIndicator(
              value: .78,
              strokeWidth: 13,
              color: green,
              backgroundColor: line,
              strokeCap: StrokeCap.round,
            ),
          ),
          const Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                '78%',
                style: TextStyle(fontSize: 30, fontWeight: FontWeight.w900),
              ),
              Text('pagos', style: TextStyle(color: muted, fontSize: 11)),
            ],
          ),
        ],
      ),
    ),
  );
}

class Settings extends StatefulWidget {
  const Settings({super.key});
  @override
  State<Settings> createState() => _SettingsState();
}

class _SettingsState extends State<Settings> {
  bool sandbox = true, sync = true, email = true, whatsapp = false;
  @override
  Widget build(BuildContext context) => Content(
    maxWidth: 1000,
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Heading(
          'PREFERÊNCIAS',
          'Configurações',
          'Gerencie loja, pagamentos e notificações.',
        ),
        const SizedBox(height: 24),
        const SettingsBlock('Dados da loja', Icons.storefront_outlined, [
          SettingsFields(),
        ]),
        const SizedBox(height: 16),
        SettingsBlock('Pagamento e Pix', Icons.account_balance_wallet_outlined, [
          SwitchListTile.adaptive(
            value: sandbox,
            onChanged: (v) => setState(() => sandbox = v),
            title: const Text('Ambiente de teste'),
            subtitle: const Text(
              'Nenhuma cobrança real será processada.',
              style: TextStyle(color: muted, fontSize: 12),
            ),
            contentPadding: EdgeInsets.zero,
            activeTrackColor: green,
          ),
          const Divider(),
          const InfoRow(
            Icons.hub_outlined,
            'Gateway',
            'Mercado Pago • conectado',
          ),
          const InfoRow(
            Icons.key_outlined,
            'Credencial',
            'APP_USR_•••••••84F2',
          ),
          const InfoRow(Icons.timer_outlined, 'Expiração do Pix', '30 minutos'),
          Container(
            padding: const EdgeInsets.all(13),
            decoration: BoxDecoration(
              color: green.withValues(alpha: .08),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: green.withValues(alpha: .25)),
            ),
            child: const Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.verified_user_outlined, color: green, size: 20),
                SizedBox(width: 10),
                Expanded(
                  child: Text(
                    'Credenciais criptografadas nunca aparecem completas no painel ou nos logs.',
                    style: TextStyle(color: muted, fontSize: 12),
                  ),
                ),
              ],
            ),
          ),
        ]),
        const SizedBox(height: 16),
        SettingsBlock('Automações', Icons.auto_awesome_outlined, [
          SwitchListTile.adaptive(
            value: sync,
            onChanged: (v) => setState(() => sync = v),
            title: const Text('Sincronizar preço e estoque'),
            subtitle: const Text(
              'Atualização via integração autorizada.',
              style: TextStyle(color: muted, fontSize: 12),
            ),
            contentPadding: EdgeInsets.zero,
            activeTrackColor: green,
          ),
          SwitchListTile.adaptive(
            value: email,
            onChanged: (v) => setState(() => email = v),
            title: const Text('Confirmação por e-mail'),
            contentPadding: EdgeInsets.zero,
            activeTrackColor: green,
          ),
          SwitchListTile.adaptive(
            value: whatsapp,
            onChanged: (v) => setState(() => whatsapp = v),
            title: const Text('Atualizações por WhatsApp'),
            contentPadding: EdgeInsets.zero,
            activeTrackColor: green,
          ),
        ]),
        const SizedBox(height: 18),
        Align(
          alignment: Alignment.centerRight,
          child: FilledButton.icon(
            onPressed: () => ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('Configurações salvas.')),
            ),
            icon: const Icon(Icons.check),
            label: const Text('Salvar alterações'),
          ),
        ),
      ],
    ),
  );
}

class SettingsBlock extends StatelessWidget {
  const SettingsBlock(this.title, this.icon, this.children, {super.key});
  final String title;
  final IconData icon;
  final List<Widget> children;
  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(22),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, color: green),
              const SizedBox(width: 10),
              Text(title, style: Theme.of(context).textTheme.titleLarge),
            ],
          ),
          const SizedBox(height: 22),
          ...children,
        ],
      ),
    ),
  );
}

class SettingsFields extends StatelessWidget {
  const SettingsFields({super.key});
  @override
  Widget build(BuildContext context) {
    const fields = [
      ('Nome da loja', 'Urban Nest'),
      ('Domínio', 'loja.urbannest.com.br'),
      ('CNPJ do recebedor', '••.•••.•••/0001-••'),
      ('Razão social', 'Urban Nest Comércio Digital Ltda.'),
    ];
    return LayoutBuilder(
      builder: (_, c) {
        final width = c.maxWidth > 620 ? (c.maxWidth - 14) / 2 : c.maxWidth;
        return Wrap(
          spacing: 14,
          runSpacing: 14,
          children: fields
              .map(
                (e) => SizedBox(
                  width: width,
                  child: TextFormField(
                    initialValue: e.$2,
                    decoration: InputDecoration(labelText: e.$1),
                  ),
                ),
              )
              .toList(),
        );
      },
    );
  }
}

class ImportDialog extends StatefulWidget {
  const ImportDialog({this.initialPage, super.key});
  final ProductPageData? initialPage;
  @override
  State<ImportDialog> createState() => _ImportDialogState();
}

class _ImportDialogState extends State<ImportDialog> {
  final controller = TextEditingController();
  final nameController = TextEditingController();
  final priceController = TextEditingController();
  final brandController = TextEditingController();
  final storeNameController = TextEditingController();
  final shippingController = TextEditingController();
  final installmentsController = TextEditingController();
  final variationsController = TextEditingController();
  final stockController = TextEditingController();
  final descriptionController = TextEditingController();
  final imagesController = TextEditingController();
  final removedReviews = <int>{};
  ProductPageData? importedPage;
  String? importError;
  int step = 0;
  bool redirect = false, loading = false, agreed = true;
  bool? mercadoLivreConnected;
  @override
  void initState() {
    super.initState();
    if (selfHosted) _loadMercadoLivreStatus();
    final initial = widget.initialPage;
    if (initial != null) {
      controller.text = initial.sourceUrl;
      setImportedPage(initial);
    }
  }

  Future<void> _loadMercadoLivreStatus() async {
    try {
      final response = await http.get(
        Uri.base.resolve('/api/mercadolivre/status'),
      );
      if (response.statusCode != 200) return;
      final status = jsonDecode(response.body) as Map<String, dynamic>;
      if (mounted) {
        setState(() => mercadoLivreConnected = status['connected'] == true);
      }
    } catch (_) {
      // A tela de configurações continua disponível caso o status falhe.
    }
  }

  @override
  void dispose() {
    controller.dispose();
    nameController.dispose();
    priceController.dispose();
    brandController.dispose();
    storeNameController.dispose();
    shippingController.dispose();
    installmentsController.dispose();
    variationsController.dispose();
    stockController.dispose();
    descriptionController.dispose();
    imagesController.dispose();
    super.dispose();
  }

  void setImportedPage(ProductPageData page) {
    importedPage = page;
    nameController.text = page.name;
    priceController.text = page.price;
    brandController.text = page.brand;
    storeNameController.text = page.storeName;
    shippingController.text = page.shippingText;
    installmentsController.text = page.installmentText;
    variationsController.text = page.variations.join('\n');
    stockController.text = page.stock >= 0 ? '${page.stock}' : '';
    descriptionController.text = page.description;
    imagesController.text = page.images.join('\n');
    removedReviews.clear();
    redirect = true;
    step = 1;
  }

  ProductPageData? editedPage() {
    final source = importedPage;
    if (source == null) return null;
    final images = imagesController.text
        .split('\n')
        .map((value) => value.trim())
        .where((value) => value.isNotEmpty)
        .toList();
    final variations = variationsController.text
        .split('\n')
        .map((value) => value.trim())
        .where((value) => value.isNotEmpty)
        .toList();
    final stock = stockController.text.trim().isEmpty
        ? -1
        : int.tryParse(stockController.text.trim());
    if (storeNameController.text.trim().isEmpty ||
        nameController.text.trim().isEmpty ||
        priceController.text.trim().isEmpty ||
        images.isEmpty ||
        images.length > 10 ||
        variations.length > 12 ||
        stock == null ||
        stock < -1) {
      setState(
        () => importError =
            'Preencha loja, título, preço e fotos; use até 12 variações e estoque válido.',
      );
      return null;
    }
    return source.edited(
      name: nameController.text.trim(),
      price: priceController.text.trim(),
      brand: brandController.text.trim(),
      storeName: storeNameController.text.trim(),
      shippingText: shippingController.text.trim(),
      installmentText: installmentsController.text.trim(),
      variations: variations,
      stock: stock,
      description: descriptionController.text.trim(),
      images: images,
      reviews: [
        for (var i = 0; i < source.reviews.length; i++)
          if (!removedReviews.contains(i)) source.reviews[i],
      ],
    );
  }

  Future<void> uploadPhotos() async {
    try {
      final picked = await FilePicker.pickFiles(
        type: FileType.custom,
        allowedExtensions: const ['jpg', 'jpeg', 'png', 'webp'],
        allowMultiple: true,
        withData: true,
      );
      if (picked == null || picked.files.isEmpty) return;
      final existing = imagesController.text
          .split('\n')
          .where((line) => line.trim().isNotEmpty)
          .length;
      if (existing + picked.files.length > 10) {
        setState(() => importError = 'Use no máximo 10 fotos por página.');
        return;
      }
      setState(() {
        loading = true;
        importError = null;
      });
      for (final file in picked.files) {
        final bytes = file.bytes;
        if (bytes == null || bytes.isEmpty || bytes.length > 10 * 1024 * 1024) {
          throw Exception('Cada foto deve ter até 10 MB.');
        }
        final response = await http.post(
          Uri.base.resolve('/api/media/upload'),
          headers: const {'Content-Type': 'application/octet-stream'},
          body: bytes,
        );
        final data = jsonDecode(response.body) as Map<String, dynamic>;
        if (response.statusCode != 200) {
          throw Exception(data['message'] ?? 'Falha ao enviar a foto.');
        }
        final url = data['url'] as String;
        imagesController.text = [
          if (imagesController.text.trim().isNotEmpty)
            imagesController.text.trim(),
          url,
        ].join('\n');
      }
      if (mounted) setState(() {});
    } catch (error) {
      if (mounted) {
        setState(
          () => importError = error.toString().replaceFirst('Exception: ', ''),
        );
      }
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final width = math.min(MediaQuery.sizeOf(context).width - 32, 680.0);
    return Dialog(
      insetPadding: const EdgeInsets.all(16),
      backgroundColor: panel,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(22),
        side: const BorderSide(color: line),
      ),
      child: SizedBox(
        width: width,
        child: ConstrainedBox(
          constraints: BoxConstraints(
            maxHeight: MediaQuery.sizeOf(context).height - 32,
          ),
          child: SingleChildScrollView(
            child: Padding(
              padding: const EdgeInsets.all(26),
              child: AnimatedSwitcher(
                duration: const Duration(milliseconds: 250),
                child: step == 0
                    ? linkStep()
                    : step == 1
                    ? reviewStep()
                    : successStep(),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget importLoadingStep() => SizedBox(
    key: const ValueKey('import-loading'),
    height: 330,
    child: Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 76,
            height: 76,
            padding: const EdgeInsets.all(20),
            decoration: BoxDecoration(
              color: green.withValues(alpha: .1),
              shape: BoxShape.circle,
              border: Border.all(color: green.withValues(alpha: .22)),
            ),
            child: const CircularProgressIndicator(
              strokeWidth: 3,
              color: green,
            ),
          ),
          const SizedBox(height: 26),
          const Text(
            'Preparando seu produto',
            style: TextStyle(fontSize: 22, fontWeight: FontWeight.w900),
          ),
          const SizedBox(height: 10),
          const Text(
            'Buscando informações, imagens e detalhes automaticamente.\nIsso pode levar alguns segundos.',
            textAlign: TextAlign.center,
            style: TextStyle(color: muted, height: 1.5),
          ),
          const SizedBox(height: 26),
          ClipRRect(
            borderRadius: BorderRadius.circular(99),
            child: const SizedBox(
              width: 220,
              child: LinearProgressIndicator(
                minHeight: 4,
                color: purple,
                backgroundColor: line,
              ),
            ),
          ),
        ],
      ),
    ),
  );

  Widget linkStep() => loading
      ? importLoadingStep()
      : Column(
          key: const ValueKey(0),
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 44,
                  height: 44,
                  decoration: BoxDecoration(
                    color: green.withValues(alpha: .12),
                    borderRadius: BorderRadius.circular(13),
                  ),
                  child: const Icon(Icons.add_link, color: green),
                ),
                const SizedBox(width: 13),
                const Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Importar produto',
                        style: TextStyle(
                          fontSize: 21,
                          fontWeight: FontWeight.w900,
                        ),
                      ),
                      Text(
                        'Comece com um link autorizado',
                        style: TextStyle(color: muted, fontSize: 12),
                      ),
                    ],
                  ),
                ),
                IconButton(
                  onPressed: () => Navigator.pop(context),
                  icon: const Icon(Icons.close),
                ),
              ],
            ),
            const SizedBox(height: 24),
            const Text(
              'URL do produto',
              style: TextStyle(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: controller,
              autofocus: true,
              decoration: InputDecoration(
                hintText: selfHosted
                    ? 'Cole o link da Shopee, Temu, AliExpress, Amazon ou Mercado Livre'
                    : 'https://www.marketplace.com.br/produto…',
                prefixIcon: Icon(Icons.link),
              ),
            ),
            const SizedBox(height: 13),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                const MarketBadge('mercado livre', Color(0xFFFFD95A)),
                const MarketBadge('amazon', Color(0xFFFFB15A)),
                const MarketBadge('shopee', Color(0xFFFF725E)),
                const MarketBadge('temu', Color(0xFFFF8D54)),
                const MarketBadge('aliexpress', Color(0xFFFF5E68)),
              ],
            ),
            if (selfHosted && mercadoLivreConnected == false)
              TextButton.icon(
                onPressed: () => launchUrl(
                  Uri.base.resolve('/api/mercadolivre/connect'),
                  mode: LaunchMode.platformDefault,
                  webOnlyWindowName: '_self',
                ),
                icon: const Icon(Icons.link),
                label: const Text('Conectar conta do Mercado Livre (uma vez)'),
              ),
            const SizedBox(height: 22),
            Container(
              padding: const EdgeInsets.all(13),
              decoration: BoxDecoration(
                color: purple.withValues(alpha: .08),
                borderRadius: BorderRadius.circular(12),
              ),
              child: const Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(Icons.info_outline, color: purple, size: 20),
                  SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      'A Nexo busca automaticamente as informações disponíveis do produto. Você poderá revisar e editar tudo antes de publicar.',
                      style: TextStyle(color: muted, fontSize: 12),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 22),
            if (importError != null) ...[
              Text(
                importError!,
                style: const TextStyle(color: red, fontSize: 12),
              ),
              const SizedBox(height: 10),
            ],
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                onPressed: loading ? null : simulate,
                icon: loading
                    ? const SizedBox(
                        width: 18,
                        height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.auto_awesome),
                label: Text(
                  loading ? 'Buscando informações…' : 'Importar informações',
                ),
                style: FilledButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 16),
                ),
              ),
            ),
          ],
        );

  Future<void> simulate() async {
    if (selfHosted) {
      if (controller.text.trim().isEmpty) {
        setState(
          () =>
              importError = 'Cole o link de um produto de uma loja compatível.',
        );
        return;
      }
      setState(() {
        loading = true;
        importError = null;
      });
      try {
        final sourceUrl = controller.text.trim();
        final capturedHtml = await captureProductHtml(sourceUrl);
        final response = await http.post(
          Uri.base.resolve('/api/products/import'),
          headers: const {'Content-Type': 'application/json'},
          body: jsonEncode({
            'url': sourceUrl,
            if (capturedHtml != null) 'html': capturedHtml,
          }),
        );
        final data = jsonDecode(response.body) as Map<String, dynamic>;
        if (response.statusCode != 200) {
          throw Exception(data['message'] ?? 'Falha ao importar o produto.');
        }
        if (!mounted) return;
        setState(() => setImportedPage(ProductPageData.fromJson(data)));
      } catch (error) {
        if (mounted) {
          setState(() => importError = friendlyImportError(error));
        }
      } finally {
        if (mounted) setState(() => loading = false);
      }
      return;
    }
    if (controller.text.trim().isEmpty) {
      controller.text = 'https://marketplace.com.br/fone-wave-pro';
    }
    setState(() => loading = true);
    await Future<void>.delayed(const Duration(milliseconds: 800));
    if (mounted) {
      setState(() {
        setImportedPage(demoProductPage);
        loading = false;
      });
    }
  }

  String friendlyImportError(Object error) {
    final message = error.toString().replaceFirst('Exception: ', '');
    final normalized = message.toLowerCase();
    if (normalized.contains('conecte sua conta') ||
        normalized.contains('autorização do mercado livre')) {
      return 'Conecte sua conta do Mercado Livre e tente novamente.';
    }
    if (normalized.contains('bloque') ||
        normalized.contains('html') ||
        normalized.contains('navegador') ||
        normalized.contains('inspeção')) {
      return 'Não conseguimos obter os dados deste produto automaticamente. Confira o link ou tente novamente em alguns minutos.';
    }
    return message;
  }

  void openPagePreview() {
    final page = editedPage();
    if (page == null) return;
    Navigator.push(
      context,
      MaterialPageRoute<void>(
        builder: (_) =>
            Storefront(page: page.withRedirect(redirect), isPreview: true),
      ),
    );
  }

  Future<void> publish() async {
    if (!selfHosted) {
      setState(() => step = 2);
      return;
    }
    final page = editedPage();
    if (page == null) return;
    setState(() {
      loading = true;
      importError = null;
    });
    try {
      final response = await http.post(
        Uri.base.resolve('/api/products/publish'),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({
          'id': page.id,
          'redirect': redirect,
          'page': page.editableJson(),
        }),
      );
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      if (response.statusCode != 200) {
        throw Exception(data['message'] ?? 'Falha ao publicar.');
      }
      if (!mounted) return;
      setState(() {
        importedPage = ProductPageData.fromJson(data);
        step = 2;
      });
    } catch (error) {
      if (mounted) {
        setState(
          () => importError = error.toString().replaceFirst('Exception: ', ''),
        );
      }
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  Widget reviewStep() {
    final page = importedPage ?? demoProductPage;
    return Column(
      key: const ValueKey(1),
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            IconButton(
              onPressed: () => setState(() => step = 0),
              icon: const Icon(Icons.arrow_back),
            ),
            const SizedBox(width: 5),
            const Expanded(
              child: Text(
                'Revise antes de publicar',
                style: TextStyle(fontSize: 21, fontWeight: FontWeight.w900),
              ),
            ),
            IconButton(
              onPressed: () => Navigator.pop(context),
              icon: const Icon(Icons.close),
            ),
          ],
        ),
        const SizedBox(height: 19),
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: purple.withValues(alpha: .08),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Text(
            selfHosted
                ? 'Este é o rascunho da sua página. Edite o conteúdo abaixo e abra a prévia para conferir como ela ficará publicada.'
                : 'Demonstração: o link ainda não é consultado. Fotos, descrição e comentários da prévia são exemplos. A prévia mostra como a página ficará com estes dados.',
            style: const TextStyle(color: muted, fontSize: 12),
          ),
        ),
        const SizedBox(height: 16),
        if (page.importNotice.isNotEmpty) ...[
          Text(
            page.importNotice,
            style: const TextStyle(color: orange, fontSize: 12),
          ),
          if (page.market == 'Mercado Livre' && page.images.isEmpty)
            Align(
              alignment: Alignment.centerLeft,
              child: Wrap(
                spacing: 8,
                runSpacing: 4,
                children: [
                  TextButton.icon(
                    onPressed: () => launchUrl(
                      Uri.base.resolve('/nexo-mercadolivre-extension.zip'),
                      mode: LaunchMode.platformDefault,
                    ),
                    icon: const Icon(Icons.extension_outlined, size: 18),
                    label: const Text('Conector para Chrome ou Edge'),
                  ),
                  TextButton.icon(
                    onPressed: () => launchUrl(
                      Uri.base.resolve('/nexo-mercadolivre-safari.zip'),
                      mode: LaunchMode.platformDefault,
                    ),
                    icon: const Icon(Icons.explore_outlined, size: 18),
                    label: const Text('Conector para Safari (Xcode)'),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 12),
        ],
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (page.images.isNotEmpty)
              Thumb(page.images.first, 108)
            else
              const SizedBox(
                width: 108,
                height: 108,
                child: ColoredBox(
                  color: soft,
                  child: Icon(Icons.add_photo_alternate_outlined, color: muted),
                ),
              ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    page.market.toUpperCase(),
                    style: const TextStyle(
                      color: purple,
                      fontSize: 10,
                      fontWeight: FontWeight.w800,
                      letterSpacing: 1,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Text(
                    page.name,
                    style: const TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    page.price,
                    style: const TextStyle(
                      color: green,
                      fontSize: 23,
                      fontWeight: FontWeight.w900,
                    ),
                  ),
                  if (page.stock >= 0)
                    Text(
                      'Estoque informado: ${page.stock} unidades',
                      style: const TextStyle(color: muted, fontSize: 11),
                    ),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 22),
        if (selfHosted) ...[
          const Text(
            'Conteúdo da sua página',
            style: TextStyle(fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: storeNameController,
            decoration: const InputDecoration(labelText: 'Nome da sua loja'),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: nameController,
            decoration: const InputDecoration(labelText: 'Título'),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: priceController,
            decoration: const InputDecoration(labelText: 'Preço exibido'),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: brandController,
            decoration: const InputDecoration(labelText: 'Marca (opcional)'),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: stockController,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(
              labelText: 'Estoque conhecido (opcional)',
            ),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: shippingController,
            decoration: const InputDecoration(
              labelText: 'Informação de frete (opcional)',
            ),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: installmentsController,
            decoration: const InputDecoration(
              labelText: 'Parcelamento (opcional)',
            ),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: variationsController,
            minLines: 1,
            maxLines: 4,
            decoration: const InputDecoration(
              labelText: 'Variações (opcional)',
              helperText: 'Uma por linha; até 12 variações.',
            ),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: descriptionController,
            minLines: 3,
            maxLines: 8,
            decoration: const InputDecoration(labelText: 'Descrição'),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: imagesController,
            minLines: 2,
            maxLines: 6,
            decoration: const InputDecoration(
              labelText: 'Fotos',
              helperText: 'Uma URL HTTPS por linha; até 10 fotos.',
            ),
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: loading ? null : uploadPhotos,
            icon: const Icon(Icons.add_photo_alternate_outlined),
            label: const Text('Adicionar fotos do computador'),
          ),
          const SizedBox(height: 16),
          Text(
            'Comentários encontrados: ${page.reviews.length}',
            style: const TextStyle(fontWeight: FontWeight.w800),
          ),
          if (page.reviews.isEmpty)
            const Padding(
              padding: EdgeInsets.only(top: 6),
              child: Text(
                'A fonte não disponibilizou comentários verificáveis.',
                style: TextStyle(color: muted, fontSize: 12),
              ),
            )
          else
            ...page.reviews.asMap().entries.map(
              (entry) => CheckboxListTile(
                value: !removedReviews.contains(entry.key),
                onChanged: (selected) => setState(() {
                  if (selected == true) {
                    removedReviews.remove(entry.key);
                  } else {
                    removedReviews.add(entry.key);
                  }
                }),
                contentPadding: EdgeInsets.zero,
                title: Text(
                  entry.value.comment,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                subtitle: Text(
                  entry.value.author,
                  style: const TextStyle(color: muted, fontSize: 11),
                ),
              ),
            ),
          const SizedBox(height: 20),
        ],
        const Text(
          'Modo de venda',
          style: TextStyle(fontWeight: FontWeight.w800),
        ),
        const SizedBox(height: 10),
        if (selfHosted)
          ModeCard(
            true,
            Icons.open_in_new,
            'Redirecionamento',
            'A compra será concluída em ${page.market}.',
            () {},
          )
        else
          Row(
            children: [
              Expanded(
                child: ModeCard(
                  !redirect,
                  Icons.credit_card,
                  'Checkout próprio',
                  'Pix e cartão na sua loja',
                  () => setState(() => redirect = false),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: ModeCard(
                  redirect,
                  Icons.open_in_new,
                  'Redirecionamento',
                  'Envia ao marketplace',
                  () => setState(() => redirect = true),
                ),
              ),
            ],
          ),
        const SizedBox(height: 14),
        if (!selfHosted)
          CheckboxListTile(
            value: agreed,
            onChanged: (v) => setState(() => agreed = v ?? false),
            contentPadding: EdgeInsets.zero,
            controlAffinity: ListTileControlAffinity.leading,
            activeColor: green,
            title: const Text(
              'Identificar Urban Nest como responsável pela venda',
              style: TextStyle(fontSize: 12),
            ),
            subtitle: const Text(
              'Obrigatório para checkout próprio.',
              style: TextStyle(color: muted, fontSize: 10),
            ),
          ),
        const SizedBox(height: 14),
        SizedBox(
          width: double.infinity,
          child: OutlinedButton.icon(
            onPressed: openPagePreview,
            icon: const Icon(Icons.visibility_outlined),
            label: const Text('Ver página completa'),
          ),
        ),
        if (page.sourceUrl.isNotEmpty) ...[
          const SizedBox(height: 8),
          SizedBox(
            width: double.infinity,
            child: TextButton.icon(
              onPressed: () => launchUrl(
                Uri.parse(page.sourceUrl),
                mode: LaunchMode.externalApplication,
              ),
              icon: const Icon(Icons.open_in_new),
              label: const Text('Comparar com a página original'),
            ),
          ),
        ],
        const SizedBox(height: 12),
        if (importError != null) ...[
          Text(importError!, style: const TextStyle(color: red, fontSize: 12)),
          const SizedBox(height: 10),
        ],
        Row(
          children: [
            Expanded(
              child: OutlinedButton(
                onPressed: () => setState(() => step = 0),
                child: const Text('Voltar ao link'),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: FilledButton.icon(
                onPressed: !loading && (agreed || redirect) ? publish : null,
                icon: const Icon(Icons.publish_outlined),
                label: Text(loading ? 'Publicando…' : 'Publicar página'),
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget successStep() => Column(
    key: const ValueKey(2),
    mainAxisSize: MainAxisSize.min,
    children: [
      Container(
        width: 76,
        height: 76,
        decoration: BoxDecoration(
          color: green.withValues(alpha: .12),
          shape: BoxShape.circle,
        ),
        child: const Icon(Icons.check, color: green, size: 42),
      ),
      const SizedBox(height: 20),
      Text(
        selfHosted ? 'Página publicada!' : 'Página demonstrativa pronta!',
        style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w900),
      ),
      const SizedBox(height: 8),
      Text(
        selfHosted
            ? 'O link abaixo já está disponível para visitantes.'
            : 'Você pode conferir a página na prévia da loja nesta sessão.',
        style: const TextStyle(color: muted),
      ),
      const SizedBox(height: 22),
      Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        decoration: BoxDecoration(
          color: ink,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: line),
        ),
        child: Row(
          children: [
            const Icon(Icons.link, color: muted, size: 18),
            const SizedBox(width: 9),
            Expanded(
              child: Text(
                Uri.base
                    .resolve('/p/${(importedPage ?? demoProductPage).slug}')
                    .toString(),
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 12),
              ),
            ),
            IconButton(
              tooltip: 'Copiar endereço',
              onPressed: () => Clipboard.setData(
                ClipboardData(
                  text: Uri.base
                      .resolve('/p/${(importedPage ?? demoProductPage).slug}')
                      .toString(),
                ),
              ),
              icon: const Icon(Icons.copy, color: green, size: 18),
            ),
          ],
        ),
      ),
      const SizedBox(height: 20),
      SizedBox(
        width: double.infinity,
        child: FilledButton(
          onPressed: () => Navigator.pop(
            context,
            (importedPage ?? demoProductPage).withRedirect(redirect),
          ),
          child: const Text('Concluir'),
        ),
      ),
    ],
  );
}

class MarketBadge extends StatelessWidget {
  const MarketBadge(this.label, this.color, {super.key});
  final String label;
  final Color color;
  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
    decoration: BoxDecoration(
      color: color.withValues(alpha: .1),
      borderRadius: BorderRadius.circular(20),
    ),
    child: Text(
      label,
      style: TextStyle(color: color, fontSize: 10, fontWeight: FontWeight.w800),
    ),
  );
}

class ModeCard extends StatelessWidget {
  const ModeCard(
    this.selected,
    this.icon,
    this.title,
    this.subtitle,
    this.onTap, {
    super.key,
  });
  final bool selected;
  final IconData icon;
  final String title, subtitle;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) => InkWell(
    onTap: onTap,
    borderRadius: BorderRadius.circular(13),
    child: Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: selected ? green.withValues(alpha: .08) : ink,
        borderRadius: BorderRadius.circular(13),
        border: Border.all(color: selected ? green : line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: selected ? green : muted),
          const SizedBox(height: 10),
          Text(
            title,
            style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 12),
          ),
          Text(subtitle, style: const TextStyle(color: muted, fontSize: 10)),
        ],
      ),
    ),
  );
}

class PublicProductPage extends StatefulWidget {
  const PublicProductPage({required this.slug, super.key});
  final String slug;

  @override
  State<PublicProductPage> createState() => _PublicProductPageState();
}

class _PublicProductPageState extends State<PublicProductPage> {
  late final Future<ProductPageData> page = loadPage();

  Future<ProductPageData> loadPage() async {
    if (!selfHosted) return demoProductPage;
    final response = await http.get(
      Uri.base.resolve('/api/products/${widget.slug}'),
    );
    if (response.statusCode != 200) throw Exception('Produto não encontrado.');
    return ProductPageData.fromJson(
      jsonDecode(response.body) as Map<String, dynamic>,
    );
  }

  @override
  Widget build(BuildContext context) => FutureBuilder<ProductPageData>(
    future: page,
    builder: (_, result) {
      if (result.hasError) {
        return const Scaffold(
          body: Center(child: Text('Produto indisponível.')),
        );
      }
      if (!result.hasData) {
        return const Scaffold(body: Center(child: CircularProgressIndicator()));
      }
      return Storefront(page: result.data!, publicPage: true);
    },
  );
}

class Storefront extends StatefulWidget {
  const Storefront({
    this.page = demoProductPage,
    this.isPreview = false,
    this.publicPage = false,
    super.key,
  });
  final ProductPageData page;
  final bool isPreview;
  final bool publicPage;
  @override
  State<Storefront> createState() => _StorefrontState();
}

class _StorefrontState extends State<Storefront> {
  int selectedColor = 0, selectedImage = 0, quantity = 1;
  bool favorite = false;
  Timer? trafficTimer;
  int get roundedRating =>
      double.tryParse(
        widget.page.rating.replaceAll(',', '.'),
      )?.round().clamp(0, 5) ??
      0;
  static const dark = Color(0xFF18201A),
      storeGreen = Color(0xFF183C24),
      gray = Color(0xFF68716A);

  Color get sourceColor => switch (widget.page.market.toLowerCase()) {
    'mercado livre' => const Color(0xFF3483FA),
    'shopee' => const Color(0xFFEE4D2D),
    'temu' => const Color(0xFFFF6900),
    'aliexpress' => const Color(0xFFE62E04),
    'amazon' => const Color(0xFFFF9900),
    _ => storeGreen,
  };

  Color get headerColor => switch (widget.page.market.toLowerCase()) {
    'mercado livre' => const Color(0xFFFFE600),
    'amazon' => const Color(0xFF131921),
    'shopee' => const Color(0xFFEE4D2D),
    'temu' => const Color(0xFFFF6900),
    'aliexpress' => const Color(0xFFE62E04),
    _ => const Color(0xFF111912),
  };

  Color get headerTextColor => widget.page.market == 'Mercado Livre'
      ? const Color(0xFF333333)
      : Colors.white;

  Color get buttonColor =>
      widget.page.market == 'Amazon' ? const Color(0xFF232F3E) : sourceColor;

  @override
  void initState() {
    super.initState();
    if (widget.publicPage && selfHosted) {
      sendTrafficEvent('view');
      trafficTimer = Timer.periodic(
        const Duration(seconds: 30),
        (_) => sendTrafficEvent('heartbeat'),
      );
    }
  }

  @override
  void dispose() {
    trafficTimer?.cancel();
    super.dispose();
  }

  Future<void> sendTrafficEvent(String kind) async {
    try {
      await http.post(
        Uri.base.resolve('/api/traffic/event'),
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({'page': '/p/${widget.page.slug}', 'kind': kind}),
      );
    } catch (_) {
      // A falha da telemetria não interrompe a página pública.
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: widget.page.isImported
          ? const Color(0xFFF7F7F7)
          : const Color(0xFFF6F7F4),
      body: SafeArea(
        child: Column(
          children: [
            if (widget.isPreview)
              Container(
                width: double.infinity,
                color: const Color(0xFFDBF5DF),
                padding: const EdgeInsets.symmetric(
                  horizontal: 24,
                  vertical: 9,
                ),
                child: Text(
                  widget.page.isImported
                      ? 'PRÉVIA DA SUA PÁGINA • ${widget.page.market}'
                      : 'PRÉVIA DA PÁGINA • conteúdo demonstrativo',
                  style: const TextStyle(
                    color: dark,
                    fontSize: 12,
                    fontWeight: FontWeight.w800,
                  ),
                  textAlign: TextAlign.center,
                ),
              ),
            _storeHeader(),
            Expanded(
              child: SingleChildScrollView(
                child: Center(
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 1180),
                    child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: Column(
                        children: [
                          Wrap(
                            spacing: 42,
                            runSpacing: 30,
                            children: [_gallery(), _details()],
                          ),
                          const SizedBox(height: 44),
                          _descriptionAndReviews(),
                          if (!widget.page.isImported) ...[
                            const SizedBox(height: 48),
                            const Align(
                              alignment: Alignment.centerLeft,
                              child: Text(
                                'Você também pode gostar',
                                style: TextStyle(
                                  color: dark,
                                  fontSize: 24,
                                  fontWeight: FontWeight.w900,
                                ),
                              ),
                            ),
                            const SizedBox(height: 18),
                            _recommendations(),
                          ],
                        ],
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _storeHeader() => Container(
    color: widget.page.isImported ? headerColor : const Color(0xFF111912),
    padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 12),
    child: Row(
      children: [
        if (!widget.publicPage) ...[
          IconButton(
            onPressed: () => Navigator.pop(context),
            icon: Icon(Icons.arrow_back, color: headerTextColor),
          ),
          const SizedBox(width: 6),
        ],
        if (widget.page.isImported)
          Container(
            width: 39,
            height: 39,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: buttonColor,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Text(
              widget.page.storeName.isEmpty
                  ? 'L'
                  : widget.page.storeName[0].toUpperCase(),
              style: const TextStyle(
                color: Colors.white,
                fontWeight: FontWeight.w900,
              ),
            ),
          )
        else
          const Logo(),
        const SizedBox(width: 10),
        Flexible(
          child: Text(
            widget.page.storeName.toUpperCase(),
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              color: headerTextColor,
              fontWeight: FontWeight.w900,
              letterSpacing: 1,
            ),
          ),
        ),
        if (widget.page.isImported) ...[
          const Spacer(),
          Text(
            widget.page.market,
            style: TextStyle(color: headerTextColor, fontSize: 12),
          ),
        ],
        if (!widget.page.isImported) const Spacer(),
        if (!widget.page.isImported) ...[
          const Icon(Icons.search),
          const SizedBox(width: 20),
          const Badge(
            label: Text('1'),
            child: Icon(Icons.shopping_bag_outlined),
          ),
        ],
      ],
    ),
  );

  Widget _gallery() => SizedBox(
    width: math.min(MediaQuery.sizeOf(context).width - 48, 510),
    child: Column(
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(widget.page.isImported ? 8 : 24),
          child: ColoredBox(
            color: Colors.white,
            child: Image.network(
              widget.page.images[selectedImage],
              height: math.min(MediaQuery.sizeOf(context).width - 48, 450),
              width: math.min(MediaQuery.sizeOf(context).width - 48, 510),
              fit: widget.page.isImported ? BoxFit.contain : BoxFit.cover,
              errorBuilder: (_, __, ___) => SizedBox(
                height: math.min(MediaQuery.sizeOf(context).width - 48, 450),
                child: const ColoredBox(
                  color: Color(0xFFE5E7E1),
                  child: Icon(Icons.broken_image_outlined, color: gray),
                ),
              ),
            ),
          ),
        ),
        const SizedBox(height: 12),
        SizedBox(
          width: double.infinity,
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: List.generate(
                widget.page.images.length,
                (i) => InkWell(
                  onTap: () => setState(() => selectedImage = i),
                  child: Container(
                    margin: const EdgeInsets.only(right: 10),
                    width: 68,
                    height: 68,
                    decoration: BoxDecoration(
                      color: const Color(0xFFE5E7E1),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(
                        color: i == selectedImage ? dark : Colors.transparent,
                        width: 2,
                      ),
                    ),
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(8),
                      child: Image.network(
                        widget.page.images[i],
                        fit: widget.page.isImported
                            ? BoxFit.contain
                            : BoxFit.cover,
                        errorBuilder: (_, __, ___) => const Icon(
                          Icons.broken_image_outlined,
                          color: gray,
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ],
    ),
  );

  Widget _details() => SizedBox(
    width: math.min(MediaQuery.sizeOf(context).width - 48, 510),
    child: DefaultTextStyle(
      style: const TextStyle(color: dark),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (widget.page.brand.isNotEmpty)
            Text(
              widget.page.brand.toUpperCase(),
              style: const TextStyle(
                color: Color(0xFF66836C),
                fontSize: 11,
                letterSpacing: 1.4,
                fontWeight: FontWeight.w800,
              ),
            ),
          const SizedBox(height: 10),
          Text(
            widget.page.name,
            style: TextStyle(
              fontSize: widget.page.isImported ? 28 : 40,
              height: 1.05,
              fontWeight: FontWeight.w900,
              letterSpacing: widget.page.isImported ? -0.3 : -1.6,
            ),
          ),
          const SizedBox(height: 14),
          if (widget.page.reviewCount > 0)
            Row(
              children: [
                Text(
                  '${'★' * roundedRating}${'☆' * (5 - roundedRating)}',
                  style: const TextStyle(
                    color: Color(0xFFFFA928),
                    letterSpacing: 2,
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  '${widget.page.rating} (${widget.page.reviewCount} avaliações)',
                  style: const TextStyle(color: gray, fontSize: 12),
                ),
              ],
            )
          else
            const Text(
              'Ainda sem avaliações',
              style: TextStyle(color: gray, fontSize: 12),
            ),
          const SizedBox(height: 22),
          Text(
            widget.page.price,
            style: TextStyle(
              fontSize: widget.page.isImported ? 36 : 30,
              fontWeight: widget.page.isImported
                  ? FontWeight.w500
                  : FontWeight.w900,
            ),
          ),
          if (widget.page.installmentText.isNotEmpty)
            Text(
              widget.page.installmentText,
              style: const TextStyle(color: gray, fontSize: 13),
            ),
          if (widget.page.variations.isNotEmpty) ...[
            const SizedBox(height: 24),
            const Text(
              'Variações',
              style: TextStyle(fontWeight: FontWeight.w800, fontSize: 13),
            ),
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: widget.page.variations
                  .asMap()
                  .entries
                  .map(
                    (entry) => ChoiceChip(
                      label: Text(entry.value),
                      selected: selectedColor == entry.key,
                      onSelected: (_) =>
                          setState(() => selectedColor = entry.key),
                    ),
                  )
                  .toList(),
            ),
          ],
          if (widget.page.shippingText.isNotEmpty) ...[
            const SizedBox(height: 24),
            Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Row(
                children: [
                  const Icon(
                    Icons.local_shipping_outlined,
                    color: Color(0xFF446E4D),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text(
                          'Entrega',
                          style: TextStyle(fontWeight: FontWeight.w800),
                        ),
                        Text(
                          widget.page.shippingText,
                          style: const TextStyle(color: gray, fontSize: 11),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ],
          const SizedBox(height: 16),
          _purchaseActions(),
          const SizedBox(height: 18),
          Row(
            children: [
              const Icon(
                Icons.verified_user_outlined,
                color: Color(0xFF446E4D),
                size: 19,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  widget.page.redirect
                      ? 'A compra será concluída em ${widget.page.market}.'
                      : 'Compra demonstrativa processada por Urban Nest.',
                  style: const TextStyle(color: gray, fontSize: 11),
                ),
              ),
            ],
          ),
        ],
      ),
    ),
  );

  Widget _purchaseActions() {
    final quantityPicker = Container(
      decoration: BoxDecoration(
        border: Border.all(color: const Color(0xFFD3D8D1)),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          IconButton(
            onPressed: quantity > 1 ? () => setState(() => quantity--) : null,
            icon: const Icon(Icons.remove, color: dark),
          ),
          Text(
            '$quantity',
            style: const TextStyle(fontWeight: FontWeight.w800),
          ),
          IconButton(
            onPressed: () => setState(() => quantity++),
            icon: const Icon(Icons.add, color: dark),
          ),
        ],
      ),
    );
    final buyButton = FilledButton(
      onPressed: checkout,
      style: FilledButton.styleFrom(
        backgroundColor: widget.page.isImported ? buttonColor : storeGreen,
        foregroundColor: Colors.white,
        padding: const EdgeInsets.symmetric(vertical: 20),
      ),
      child: Text(
        widget.page.redirect ? 'Ver no marketplace' : 'Comprar agora',
        style: const TextStyle(fontWeight: FontWeight.w900),
      ),
    );
    final favoriteButton = IconButton.outlined(
      onPressed: () => setState(() => favorite = !favorite),
      icon: Icon(
        favorite ? Icons.favorite : Icons.favorite_border,
        color: favorite ? red : dark,
      ),
    );
    if (widget.page.isImported && widget.page.redirect) {
      return SizedBox(width: double.infinity, child: buyButton);
    }
    if (MediaQuery.sizeOf(context).width < 600) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(children: [quantityPicker, const Spacer(), favoriteButton]),
          const SizedBox(height: 12),
          buyButton,
        ],
      );
    }
    return Row(
      children: [
        quantityPicker,
        const SizedBox(width: 12),
        Expanded(child: buyButton),
        const SizedBox(width: 10),
        favoriteButton,
      ],
    );
  }

  Widget _descriptionAndReviews() => Align(
    alignment: Alignment.centerLeft,
    child: ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 1100),
      child: DefaultTextStyle(
        style: const TextStyle(color: dark),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Sobre o produto',
              style: TextStyle(fontSize: 24, fontWeight: FontWeight.w900),
            ),
            const SizedBox(height: 12),
            Text(
              widget.page.description,
              style: const TextStyle(fontSize: 15, height: 1.5),
            ),
            const SizedBox(height: 10),
            Text(
              widget.page.stock >= 0
                  ? 'Origem: ${widget.page.market} • Estoque: ${widget.page.stock} unidades'
                  : 'Origem: ${widget.page.market}',
              style: const TextStyle(color: gray, fontSize: 13),
            ),
            const SizedBox(height: 34),
            Text(
              widget.page.reviewCount > 0
                  ? 'Avaliações de clientes • ${widget.page.rating} de 5'
                  : 'Avaliações de clientes',
              style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w900),
            ),
            const SizedBox(height: 8),
            Text(
              '${widget.page.reviewCount} avaliações • ${widget.page.reviews.length} comentários exibidos',
              style: const TextStyle(color: gray, fontSize: 13),
            ),
            const SizedBox(height: 16),
            if (widget.page.reviews.isEmpty)
              const Text(
                'Ainda não há comentários para este produto.',
                style: TextStyle(color: gray),
              )
            else
              ...widget.page.reviews.map(
                (review) => Container(
                  width: double.infinity,
                  margin: const EdgeInsets.only(bottom: 10),
                  padding: const EdgeInsets.all(18),
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(14),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              review.author,
                              style: const TextStyle(
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                          ),
                          Text(
                            review.date,
                            style: const TextStyle(color: gray, fontSize: 12),
                          ),
                        ],
                      ),
                      const SizedBox(height: 5),
                      Text(
                        '★' * review.stars,
                        style: const TextStyle(color: Color(0xFFFFA928)),
                      ),
                      const SizedBox(height: 8),
                      Text(review.comment, style: const TextStyle(height: 1.4)),
                    ],
                  ),
                ),
              ),
          ],
        ),
      ),
    ),
  );

  Widget _recommendations() => SizedBox(
    height: 255,
    child: ListView.separated(
      scrollDirection: Axis.horizontal,
      itemCount: 4,
      separatorBuilder: (_, __) => const SizedBox(width: 14),
      itemBuilder: (_, i) => SizedBox(
        width: 210,
        child: Card(
          color: Colors.white,
          child: Padding(
            padding: const EdgeInsets.all(10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                ClipRRect(
                  borderRadius: BorderRadius.circular(10),
                  child: Image.network(
                    products[i + 1].image,
                    width: double.infinity,
                    height: 150,
                    fit: BoxFit.cover,
                    errorBuilder: (_, __, ___) => const SizedBox(
                      height: 150,
                      child: ColoredBox(
                        color: Color(0xFFE5E7E1),
                        child: Icon(Icons.broken_image_outlined, color: gray),
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: 10),
                Text(
                  products[i + 1].name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: dark,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                Text(
                  products[i + 1].price,
                  style: const TextStyle(
                    color: storeGreen,
                    fontWeight: FontWeight.w900,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    ),
  );

  void checkout() {
    if (widget.page.redirect && widget.page.sourceUrl.isNotEmpty) {
      launchUrl(
        Uri.parse(widget.page.sourceUrl),
        mode: LaunchMode.externalApplication,
      );
      return;
    }
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.white,
      showDragHandle: true,
      builder: (_) => Padding(
        padding: const EdgeInsets.fromLTRB(24, 8, 24, 32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              widget.page.redirect ? Icons.open_in_new : Icons.pix,
              size: 50,
              color: storeGreen,
            ),
            const SizedBox(height: 12),
            Text(
              widget.page.redirect
                  ? 'Ir para o marketplace'
                  : 'Checkout seguro',
              style: const TextStyle(
                color: dark,
                fontSize: 22,
                fontWeight: FontWeight.w900,
              ),
            ),
            const SizedBox(height: 7),
            Text(
              widget.page.redirect
                  ? 'Na integração real, você será enviado para ${widget.page.market}.'
                  : 'Na integração real, o Pix dinâmico será emitido pelo PSP configurado.',
              textAlign: TextAlign.center,
              style: TextStyle(color: gray),
            ),
            const SizedBox(height: 20),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: () => Navigator.pop(context),
                style: FilledButton.styleFrom(backgroundColor: storeGreen),
                child: const Text('Continuar no modo demonstração'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
