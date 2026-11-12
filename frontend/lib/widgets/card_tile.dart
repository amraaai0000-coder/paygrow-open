/// One product card. The number is the loudest thing on it, because that number
/// is what the shopper says out loud.

import 'package:flutter/material.dart';

import '../models.dart';
import '../theme.dart';

/// One product card. The number in the corner is what the shopper says out
/// loud ("book card 2"), so it is deliberately the loudest thing on the tile.
class CardTile extends StatelessWidget {
  const CardTile({
    super.key,
    required this.card,
    required this.onBook,
  });

  final AgentCard card;
  final VoidCallback onBook;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 168,
      decoration: BoxDecoration(
        color: PayGrow.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: PayGrow.line),
      ),
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Stack(
            children: [
              SizedBox(
                height: 110,
                width: double.infinity,
                child: card.image == null || card.image!.isEmpty
                    ? _placeholder()
                    : Image.network(
                        card.image!,
                        fit: BoxFit.cover,
                        errorBuilder: (_, __, ___) => _placeholder(),
                        loadingBuilder: (context, child, progress) =>
                            progress == null ? child : _placeholder(),
                      ),
              ),
              Positioned(
                top: 8,
                left: 8,
                child: Container(
                  width: 24,
                  height: 24,
                  alignment: Alignment.center,
                  decoration: const BoxDecoration(
                    color: PayGrow.orange,
                    shape: BoxShape.circle,
                  ),
                  child: Text(
                    '${card.cardNo}',
                    style: const TextStyle(
                      color: PayGrow.black,
                      fontSize: 12,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                ),
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(10, 10, 10, 10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  height: 34,
                  child: Text(
                    card.title,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: PayGrow.white,
                      fontSize: 12,
                      height: 1.35,
                    ),
                  ),
                ),
                const SizedBox(height: 6),
                Text(
                  '${card.money} × ${card.quantity.toStringAsFixed(card.quantity % 1 == 0 ? 0 : 1)}',
                  style: const TextStyle(
                    color: PayGrow.white,
                    fontSize: 13,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  '${card.priceSource} · ${card.availability.replaceAll('_', ' ')}',
                  style: const TextStyle(color: PayGrow.grey, fontSize: 10),
                ),
                const SizedBox(height: 8),
                SizedBox(
                  width: double.infinity,
                  height: 32,
                  child: ElevatedButton(
                    onPressed: onBook,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: PayGrow.orange,
                      foregroundColor: PayGrow.black,
                      padding: EdgeInsets.zero,
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(9),
                      ),
                    ),
                    child: Text(
                      'Book ${card.cardNo}',
                      style: const TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
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

  Widget _placeholder() => Container(
        color: PayGrow.surfaceAlt,
        alignment: Alignment.center,
        child: const Icon(Icons.image_not_supported_outlined,
            color: PayGrow.grey, size: 22),
      );
}
