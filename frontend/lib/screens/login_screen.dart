/// Email login with a one time code. No password to remember.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../local_store.dart';
import '../theme.dart';
import 'home_screen.dart';

/// Email login with a one-time code. There is no password to remember.
class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, this.onSignedIn});

  final VoidCallback? onSignedIn;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  static const int _digits = 6;

  final PayGrowApi _api = PayGrowApi.instance;
  final TextEditingController _email = TextEditingController();
  final List<TextEditingController> _code =
      List.generate(_digits, (_) => TextEditingController());
  final List<FocusNode> _focus = List.generate(_digits, (_) => FocusNode());

  bool _codeSent = false;
  bool _busy = false;
  String? _devCode;
  String? _error;

  @override
  void dispose() {
    _email.dispose();
    for (final controller in _code) {
      controller.dispose();
    }
    for (final node in _focus) {
      node.dispose();
    }
    super.dispose();
  }

  String get _enteredCode => _code.map((c) => c.text.trim()).join();

  Future<void> _sendCode() async {
    final email = _email.text.trim();
    if (!email.contains('@') || !email.contains('.')) {
      setState(() => _error = 'Enter a valid email address.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final devCode = await _api.requestCode(email);
      setState(() {
        _codeSent = true;
        _devCode = devCode;
      });
      WidgetsBinding.instance
          .addPostFrameCallback((_) => _focus.first.requestFocus());
    } catch (e) {
      setState(() => _error = 'Could not send the code: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _verify() async {
    final email = _email.text.trim();
    final code = _enteredCode;
    if (code.length < _digits) {
      setState(() => _error = 'Enter all $_digits digits.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final user = await _api.verifyCode(email, code);
      await LocalStore.write({
        'token': '${user['token']}',
        'email': '${user['email']}',
        'deviceId': _api.deviceId,
      });
      _goHome();
    } catch (e) {
      setState(() => _error = '$e');
      for (final controller in _code) {
        controller.clear();
      }
      _focus.first.requestFocus();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _goHome() {
    if (widget.onSignedIn != null) {
      widget.onSignedIn!();
      return;
    }
    Navigator.of(context).pushAndRemoveUntil(
      MaterialPageRoute(builder: (_) => const HomeScreen()),
      (_) => false,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: PayGrow.black,
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(
            PayGrow.pageMargin,
            40,
            PayGrow.pageMargin,
            24,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Center(
                child: Image.asset(
                  'assets/logo.png',
                  height: 96,
                  fit: BoxFit.contain,
                ),
              ),
              const SizedBox(height: 14),
              Center(
                child: RichText(
                  text: const TextSpan(
                    style: TextStyle(
                      fontSize: 26,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 0.2,
                    ),
                    children: [
                      TextSpan(
                        text: 'Pay',
                        style: TextStyle(color: PayGrow.white),
                      ),
                      TextSpan(
                        text: 'Grow',
                        style: TextStyle(color: PayGrow.green),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 6),
              Center(
                child: Text(
                  _codeSent
                      ? 'Enter the $_digits-digit code we emailed you'
                      : 'Sign in with your email',
                  style: const TextStyle(color: PayGrow.grey, fontSize: 12),
                ),
              ),
              const SizedBox(height: 26),
              _field(),
              const SizedBox(height: 14),
              if (!_codeSent)
                _primary('Send code', _busy ? null : _sendCode)
              else ...[
                _codeRow(),
                if (_devCode != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 10),
                    child: Text(
                      'Test code: $_devCode',
                      style: const TextStyle(
                        color: PayGrow.orange,
                        fontSize: 12,
                      ),
                    ),
                  ),
                const SizedBox(height: 14),
                _primary('Verify and continue', _busy ? null : _verify),
                const SizedBox(height: 6),
                TextButton(
                  onPressed: _busy
                      ? null
                      : () => setState(() {
                            _codeSent = false;
                            for (final controller in _code) {
                              controller.clear();
                            }
                          }),
                  child: const Text(
                    'Use a different email',
                    style: TextStyle(color: PayGrow.grey, fontSize: 12),
                  ),
                ),
              ],
              if (_error != null) ...[
                const SizedBox(height: 12),
                Text(
                  _error!,
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: PayGrow.orange, fontSize: 12),
                ),
              ],
              const SizedBox(height: 30),
              // Guest mode: the same box treatment, deliberately quieter.
              OutlinedButton(
                onPressed: _busy ? null : _goHome,
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size.fromHeight(48),
                  side: BorderSide(color: PayGrow.line),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(16),
                  ),
                ),
                child: const Text(
                  'Skip for now',
                  style: TextStyle(
                    color: PayGrow.white,
                    fontSize: 13.5,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              const SizedBox(height: 8),
              const Center(
                child: Text(
                  'You can keep shopping as a guest',
                  style: TextStyle(color: PayGrow.grey, fontSize: 11),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _field() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      decoration: BoxDecoration(
        color: PayGrow.surfaceAlt,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: PayGrow.line),
      ),
      child: TextField(
        controller: _email,
        keyboardType: TextInputType.emailAddress,
        enabled: !_codeSent,
        style: const TextStyle(color: PayGrow.white, fontSize: 14),
        decoration: const InputDecoration(
          border: InputBorder.none,
          hintText: 'you@example.com',
          hintStyle: TextStyle(color: PayGrow.grey, fontSize: 13.5),
        ),
      ),
    );
  }

  /// One box per digit, so the shopper can see how many the code has.
  Widget _codeRow() {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: List.generate(_digits, (index) {
        return SizedBox(
          width: 44,
          height: 54,
          child: Container(
            decoration: BoxDecoration(
              color: PayGrow.surfaceAlt,
              borderRadius: BorderRadius.circular(14),
              border: Border.all(
                color: _code[index].text.isEmpty
                    ? PayGrow.line
                    : PayGrow.green.withValues(alpha: 0.6),
              ),
            ),
            child: TextField(
              controller: _code[index],
              focusNode: _focus[index],
              autofocus: index == 0,
              textAlign: TextAlign.center,
              keyboardType: TextInputType.number,
              inputFormatters: [
                FilteringTextInputFormatter.digitsOnly,
                LengthLimitingTextInputFormatter(1),
              ],
              style: const TextStyle(
                color: PayGrow.white,
                fontSize: 20,
                fontWeight: FontWeight.w600,
              ),
              decoration: const InputDecoration(
                border: InputBorder.none,
                counterText: '',
              ),
              onChanged: (value) {
                setState(() {});
                if (value.isNotEmpty && index < _digits - 1) {
                  _focus[index + 1].requestFocus();
                }
                if (value.isEmpty && index > 0) {
                  _focus[index - 1].requestFocus();
                }
                if (_enteredCode.length == _digits) _verify();
              },
            ),
          ),
        );
      }),
    );
  }

  Widget _primary(String label, VoidCallback? onTap) {
    return SizedBox(
      height: 50,
      child: ElevatedButton(
        onPressed: onTap,
        style: ElevatedButton.styleFrom(
          backgroundColor: PayGrow.green,
          foregroundColor: PayGrow.black,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
          ),
        ),
        child: Text(
          label,
          style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700),
        ),
      ),
    );
  }
}
